// ft_exporter: exports The Finals' game files for Finals Texturer, the way FModel does, using FModel's setup.
//
//   ft_exporter check
//       FModel setup found? (JSON on stdout; never prints the key)
//   ft_exporter export --out <Exports folder> [--force] [--only] <game path>...
//       Exports each package, every package next to it (store image, metadata, persistence) and everything
//       they reference that the add-on uses (meshes, materials up to the master, textures, curve atlases,
//       data assets), into <out> with FModel's folder layout: properties as .json ("Save Properties"),
//       meshes as .psk/.pskx, textures as .png (texture arrays: _LAYER<n>.png).
//       Game paths: /Game/... or Discovery/Content/... (with or without extension); a folder exports
//       everything under it, <folder>/* its own files, <folder>/<prefix>* its files starting with <prefix>. Existing meshes/textures and full JSONs are kept (--force rewrites them);
//       JSONs flattened by FModel's mesh export are replaced.
//       --only: just the listed files' textures / meshes (no .json, nothing next to or referenced by them);
//       the add-on's store-image browser uses it to fetch T_UI_* images into its own cache.
//
// Output: one JSON object per line on stdout ({"event": ...}), for the add-on's progress bar.
using System.Text.RegularExpressions;
using CUE4Parse.Compression;
using CUE4Parse.Encryption.Aes;
using CUE4Parse.FileProvider;
using CUE4Parse.FileProvider.Objects;
using CUE4Parse.MappingsProvider.Usmap;
using CUE4Parse.UE4.Assets;
using CUE4Parse.UE4.Assets.Exports;
using CUE4Parse.UE4.Assets.Exports.Texture;
using CUE4Parse.UE4.Objects.Core.Misc;
using CUE4Parse.UE4.Versions;
using CUE4Parse_Conversion;
using CUE4Parse_Conversion.Options;
using CUE4Parse_Conversion.Textures.BC;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;

namespace FtExporter;

static class Program
{
    static int Main(string[] args)
    {
        Console.OutputEncoding = new System.Text.UTF8Encoding(false);
        try
        {
            if (args.Length == 0 || args[0] is "-h" or "--help") { Usage(); return 2; }
            string? settings = Opt(ref args, "--settings");
            var setup = Setup.Load(settings);
            if (Opt(ref args, "--usmap") is { } usmap) setup.Mappings = usmap;
            return args[0] switch
            {
                "check" => Check(setup),
                "export" => Export(setup, args.Skip(1).ToArray()),
                "list" => List(setup, args.Skip(1).ToArray()),
                _ => Fail($"unknown command {args[0]}"),
            };
        }
        catch (Exception e)
        {
            return Fail(e.Message);
        }
    }

    static void Usage() =>
        Console.Error.WriteLine("usage: ft_exporter check | export --out <dir> [--force] [--only] <game path>...");

    static string? Opt(ref string[] args, string name)
    {
        int i = Array.IndexOf(args, name);
        if (i < 0 || i + 1 >= args.Length) return null;
        var v = args[i + 1];
        args = args.Take(i).Concat(args.Skip(i + 2)).ToArray();
        return v;
    }

    internal static void Emit(object o)
    {
        Console.Out.WriteLine(JsonConvert.SerializeObject(o, Formatting.None));
        Console.Out.Flush();
    }

    static int Fail(string msg)
    {
        Emit(new { @event = "error", message = msg });
        return 1;
    }

    static int Check(Setup s)
    {
        Emit(new
        {
            @event = "check",
            ok = s.Problems.Count == 0,
            problems = s.Problems,
            settings = s.SettingsPath,
            game_dir = s.GameDir,
            mappings = s.Mappings,
            has_key = s.AesKey.Length > 0,
            oodle = s.Oodle.Length > 0,
            detex = s.Detex.Length > 0,
            fmodel_exports = s.FModelExports,
        });
        return s.Problems.Count == 0 ? 0 : 1;
    }

    static DefaultFileProvider Mount(Setup s)
    {
        if (s.Problems.Count > 0) throw new Exception(string.Join(" ", s.Problems));
        OodleHelper.Initialize(s.Oodle);
        if (s.Zlib.Length > 0) ZlibHelper.Initialize(s.Zlib);
        if (s.Detex.Length > 0) DetexHelper.Initialize(s.Detex);
        var provider = new DefaultFileProvider(s.PaksDir,
            s.PaksDir == s.GameDir ? SearchOption.AllDirectories : SearchOption.TopDirectoryOnly,
            new VersionContainer((EGame)s.UeVersion), StringComparer.OrdinalIgnoreCase);
        provider.MappingsContainer = new FileUsmapTypeMappingsProvider(s.Mappings);
        provider.Initialize();
        provider.SubmitKey(new FGuid(), new FAesKey(s.AesKey));
        foreach (var (g, k) in s.DynamicKeys)
            provider.SubmitKey(new FGuid(g), new FAesKey(k));
        provider.PostMount();
        return provider;
    }

    // list <prefix> [--depth n]: package paths under a game folder (one per line, as JSON events), or with
    // --depth the sub-folders and how many packages each holds
    static int List(Setup s, string[] args)
    {
        var a = args.ToArray();
        int depth = int.TryParse(Opt(ref a, "--depth"), out var d) ? d : 0;
        var prefix = (a.FirstOrDefault() ?? "").Trim('/');
        var provider = Mount(s);
        var files = provider.Files.Values.Where(f => f.Extension is "uasset" or "umap"
            && f.Path.StartsWith(prefix, StringComparison.OrdinalIgnoreCase)).Select(f => f.Path).ToList();
        if (depth == 0)
        {
            foreach (var f in files.OrderBy(x => x)) Emit(new { @event = "file", path = f });
        }
        else
        {
            int skip = prefix.Length == 0 ? 0 : prefix.Split('/').Length;
            foreach (var g in files.GroupBy(f => string.Join('/', f.Split('/').Skip(skip).Take(depth)))
                         .OrderBy(g => g.Key))
                Emit(new { @event = "folder", path = g.Key, count = g.Count() });
        }
        return 0;
    }

    static int Export(Setup s, string[] args)
    {
        var a = args.ToArray();
        var outDir = Opt(ref a, "--out") ?? throw new Exception("--out <folder> is required");
        bool force = a.Contains("--force");
        bool only = a.Contains("--only");
        var paths = a.Where(x => x != "--force" && x != "--only").ToList();
        if (paths.Count == 0) throw new Exception("nothing to export");

        Emit(new { @event = "status", message = "Opening the game files..." });
        var provider = Mount(s);
        var options = new ExportOptions(
            meshFormat: EMeshFormat.ActorX,
            naniteMeshFormat: ENaniteMeshFormat.NoNanite,
            meshQuality: EMeshQuality.Highest,
            texturePlatform: (ETexturePlatform)s.TexturePlatform,
            textureFormat: ETextureFormat.Png,
            exportMaterials: false,
            exportMorphTargets: s.MorphTargets,
            socketFormat: (ESocketFormat)s.SocketFormat);
        var job = new Job(provider, outDir, force, options) { Only = only };
        foreach (var p in paths) job.AddStart(p);
        job.Run();
        return job.Failed > 0 && job.Written == 0 ? 1 : 0;
    }
}

sealed class Job
{
    readonly DefaultFileProvider _p;
    readonly string _out;
    readonly bool _force;
    readonly ExportOptions _options;
    readonly Queue<(GameFile File, int Depth)> _queue = new();
    readonly HashSet<string> _seen = new(StringComparer.OrdinalIgnoreCase);
    readonly HashSet<string> _roots = new(StringComparer.OrdinalIgnoreCase);         // e.g. …/Discovery/Items
    readonly HashSet<string> _fromBlueprint = new(StringComparer.OrdinalIgnoreCase);
    readonly HashSet<string> _bpParents = new(StringComparer.OrdinalIgnoreCase);
    readonly HashSet<string> _texturesOnly = new(StringComparer.OrdinalIgnoreCase);   // reached from a master
    readonly List<(UObject Obj, GameFile File)> _assets = new();
    readonly JsonSerializerSettings _json = new()
        { Formatting = Formatting.Indented, ReferenceLoopHandling = ReferenceLoopHandling.Ignore };
    public int Written, Skipped, Failed;
    public bool Only;                 // just the start files themselves: no JSON, no siblings, no references

    static readonly Regex RefRx = new("\"(?:ObjectPath|AssetPathName)\"\\s*:\\s*\"(/[^\"]+)\"", RegexOptions.Compiled);

    // how far data assets lead to other data assets (skin → visual → part lists); meshes, materials,
    // textures and curves are always followed
    const int MaxDataDepth = 3;

    public Job(DefaultFileProvider p, string outDir, bool force, ExportOptions options)
    {
        _p = p; _out = outDir; _force = force; _options = options;
    }

    Dictionary<string, string>? _mounts;

    // "/<Mount>/…" → the folder its files live in ("/Game/" → "Discovery/Content", plugins such as
    // "/EmbarkScript/" → "RemappedPlugins/EmbarkScript/Content"), from the game's own file list
    string? MountPrefix(string mount)
    {
        if (_mounts == null)
        {
            _mounts = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);
            foreach (var path in _p.Files.Values.Select(f => f.Path))
            {
                int c = path.IndexOf("/Content/", StringComparison.OrdinalIgnoreCase);
                if (c <= 0) continue;
                var prefix = path[..(c + 8)];
                var name = prefix[..c];
                name = name[(name.LastIndexOf('/') + 1)..];
                if (!_mounts.TryGetValue(name, out var old) || prefix.Length < old.Length) _mounts[name] = prefix;
            }
        }
        return _mounts.TryGetValue(mount, out var p) ? p : null;
    }

    string ToFilePath(string gamePath)
    {
        // gamePath: "/Mount/rest" (no extension)
        var parts = gamePath.TrimStart('/').Split('/', 2);
        if (parts.Length == 2 && !parts[0].Equals("Game", StringComparison.OrdinalIgnoreCase)
            && MountPrefix(parts[0]) is { } prefix)
            return prefix + "/" + parts[1];
        return _p.FixPath(gamePath);                              // /Game/… → Discovery/Content/…
    }

    GameFile? Find(string path)
    {
        var x = path.Trim().Replace('\\', '/');
        int dot = x.LastIndexOf('.');
        if (dot > x.LastIndexOf('/')) x = x[..dot];            // ".uasset" / ".ObjectName" / ".0"
        if (x.StartsWith("/")) x = ToFilePath(x);
        if (!x.EndsWith(".uasset", StringComparison.OrdinalIgnoreCase) && !x.EndsWith(".umap", StringComparison.OrdinalIgnoreCase))
            x += ".uasset";
        return _p.Files.TryGetValue(x, out var f) ? f : null;
    }

    public void AddStart(string path)
    {
        var norm = path.Trim().Replace('\\', '/');
        if (norm.EndsWith("/*"))
        {
            // a folder's own files (not its subfolders)
            var d = norm[..^2];
            if (d.StartsWith("/")) d = _p.FixPath(d + "/x").TrimEnd('x', '/');
            var own = _p.Files.Values.Where(f => f.Extension == "uasset"
                && f.Path.StartsWith(d + "/", StringComparison.OrdinalIgnoreCase)
                && f.Path.IndexOf('/', d.Length + 1) < 0).ToList();
            if (own.Count == 0) Program.Emit(new { @event = "warning", message = $"{path} isn't in the game files" });
            foreach (var f in own) Enqueue(f, 0);
            return;
        }
        int slash = norm.LastIndexOf('/');
        if (norm.EndsWith("*") && slash >= 0 && slash < norm.Length - 2)
        {
            // "<folder>/<name prefix>*": that folder's files whose name starts with the prefix
            // (faces: <face folder>/T_UI_* = its store image, whose exact name isn't recorded anywhere)
            var d = norm[..slash];
            var stem = norm[(slash + 1)..^1];
            if (d.StartsWith("/")) d = _p.FixPath(d + "/x").TrimEnd('x', '/');
            var hits = _p.Files.Values.Where(f => f.Extension == "uasset"
                && f.Path.StartsWith(d + "/", StringComparison.OrdinalIgnoreCase)
                && f.Path.IndexOf('/', d.Length + 1) < 0
                && f.NameWithoutExtension.StartsWith(stem, StringComparison.OrdinalIgnoreCase)).ToList();
            if (hits.Count == 0) Program.Emit(new { @event = "warning", message = $"{path} isn't in the game files" });
            foreach (var f in hits) Enqueue(f, 0);
            return;
        }
        var file = Find(path);
        if (file == null)
        {
            // a folder: everything under it
            var prefix = path.Trim().Replace('\\', '/').TrimEnd('/');
            if (prefix.StartsWith("/")) prefix = _p.FixPath(prefix + "/x").TrimEnd('x', '/');
            var under = _p.Files.Values.Where(f => f.Extension == "uasset"
                && f.Path.StartsWith(prefix + "/", StringComparison.OrdinalIgnoreCase)).ToList();
            if (under.Count == 0) { Program.Emit(new { @event = "warning", message = $"{path} isn't in the game files" }); return; }
            foreach (var f in under) Enqueue(f, 0);
            return;
        }
        Enqueue(file, 0);
        if (Only) return;
        // a skin / face: the files next to it (store image T_UI_*, DA_MetaData_* names, persistence…);
        // a Blueprint (game object) has nothing of that kind
        if (file.NameWithoutExtension.StartsWith("BP_", StringComparison.OrdinalIgnoreCase)) return;
        var dir = file.Path[..file.Path.LastIndexOf('/')];
        foreach (var f in _p.Files.Values.Where(f => f.Extension == "uasset"
                     && f.Path.StartsWith(dir + "/", StringComparison.OrdinalIgnoreCase)
                     && f.Path.IndexOf('/', dir.Length + 1) < 0))
            Enqueue(f, 0);
    }

    // the content category a start file lives in ("Discovery/Content/Discovery/Characters"): data
    // assets lead to other data assets only inside it (a weapon's own data asset also lists its
    // animations, sounds, damage curves…)
    static string Root(string path)
    {
        var parts = path.Split('/');
        return string.Join('/', parts.Take(Math.Min(4, parts.Length - 1)));
    }

    void Enqueue(GameFile f, int depth)
    {
        if (depth == 0) _roots.Add(Root(f.Path));
        if (_seen.Add(f.Path)) _queue.Enqueue((f, depth));
    }

    enum Kind { Skip, Mesh, Texture, Material, Master, Curve, Data, Blueprint }

    static Kind Classify(string cls)
    {
        if (cls.EndsWith("BlueprintGeneratedClass")) return Kind.Blueprint;
        if (cls is "SkeletalMesh" or "StaticMesh") return Kind.Mesh;
        if (cls is "MaterialInstanceConstant" or "MaterialInstanceDynamic") return Kind.Material;
        if (cls == "Material") return Kind.Master;
        if (cls.StartsWith("Curve")) return Kind.Curve;           // CurveLinearColor(Atlas), CurveFloat
        if (cls.Contains("Texture") && !cls.Contains("RenderTarget") && !cls.Contains("Media")) return Kind.Texture;
        if (cls.Contains("Blueprint") || cls.Contains("Skeleton") || cls.Contains("Physics") || cls.StartsWith("Anim")
            || cls.StartsWith("Niagara") || cls.StartsWith("Sound") || cls.Contains("MaterialFunction")
            || cls.Contains("ParameterCollection") || cls is "World" or "Font" or "DataTable" or "Level")
            return Kind.Skip;
        return Kind.Data;
    }

    public void Run()
    {
        // 1. properties (.json) + references
        int n = 0;
        while (_queue.Count > 0)
        {
            var (file, depth) = _queue.Dequeue();
            n++;
            if (n % 10 == 0) Program.Emit(new { @event = "scan", done = n, queued = _queue.Count, path = file.Path });
            IPackage pkg;
            try { pkg = _p.LoadPackage(file); }
            catch (Exception e) { Fail(file.Path, e); continue; }
            List<UObject> exports;
            try { exports = pkg.GetExports().ToList(); }
            catch (Exception e) { Fail(file.Path, e); continue; }
            var main = exports.FirstOrDefault(e => e.Name == file.NameWithoutExtension)
                       ?? exports.FirstOrDefault(e => e.Name == file.NameWithoutExtension + "_C")
                       ?? exports.FirstOrDefault();
            if (main == null) continue;
            var kind = Classify(main.ExportType);
            if (kind == Kind.Skip && depth > 0) continue;
            // a Blueprint's references: its parts (meshes, materials, textures) and its parent Blueprint
            // (inherited parts), not the gameplay classes, sounds, effects… it also points at
            if (_fromBlueprint.Contains(file.Path) && !_bpParents.Contains(file.Path)
                && kind is Kind.Blueprint or Kind.Data or Kind.Skip) continue;
            // masters reference textures, but also functions, parameter collections…: keep the textures
            if (_texturesOnly.Contains(file.Path) && kind is not (Kind.Texture or Kind.Curve)) continue;

            if (Only)
            {
                if (kind is Kind.Mesh or Kind.Texture) _assets.Add((main, file));
                continue;
            }
            string json;
            try { json = JsonConvert.SerializeObject(exports, _json); }
            catch (Exception e) { Fail(file.Path, e); continue; }
            WriteJson(file, json);

            if (kind is Kind.Mesh or Kind.Texture && main.ExportType != "CurveLinearColorAtlas")
                _assets.Add((main, file));
            if (kind == Kind.Skip) continue;
            if (kind == Kind.Data && depth >= MaxDataDepth) continue;

            if (kind == Kind.Blueprint && main is CUE4Parse.UE4.Objects.UObject.UStruct st
                && st.SuperStruct?.ResolvedObject?.GetPathName() is { } super && Find(super) is { } pf)
                _bpParents.Add(pf.Path);
            foreach (Match m in RefRx.Matches(json))
            {
                var r = m.Groups[1].Value;
                if (r.StartsWith("/Script/") || r.StartsWith("/Engine/")) continue;
                var f = Find(r);
                if (f == null || _seen.Contains(f.Path)) continue;
                if (kind == Kind.Master) _texturesOnly.Add(f.Path);
                if (kind == Kind.Blueprint) _fromBlueprint.Add(f.Path);
                if (kind == Kind.Data && f.NameWithoutExtension.StartsWith("DA_", StringComparison.OrdinalIgnoreCase)
                    && !_roots.Contains(Root(f.Path))) continue;
                Enqueue(f, kind == Kind.Data ? depth + 1 : depth);
            }
        }
        Program.Emit(new { @event = "scan", done = n, queued = 0, path = "" });

        // 2. meshes and textures
        int i = 0;
        foreach (var (obj, file) in _assets)
        {
            i++;
            Program.Emit(new { @event = "progress", done = i, total = _assets.Count, path = file.Path });
            if (!_force && AssetExists(file)) { Skipped++; continue; }
            try
            {
                var session = new ExportSession(null);
                session.Add(obj);
                var results = session.RunAsync(_out, _options, null!, CancellationToken.None).GetAwaiter().GetResult();
                foreach (var r in results)
                {
                    if (r.Success) Written += r.DiskFilePaths.Count;
                    else Fail(r.ObjectPath, r.Error);
                }
            }
            catch (Exception e) { Fail(file.Path, e); }
        }
        Program.Emit(new { @event = "done", written = Written, skipped = Skipped, failed = Failed,
                           packages = n, assets = _assets.Count });
    }

    string OutPath(GameFile f, string ext) =>
        Path.Combine(_out, f.PathWithoutExtension.Replace('/', Path.DirectorySeparatorChar) + ext);

    void WriteJson(GameFile f, string json)
    {
        var dst = OutPath(f, ".json");
        if (!_force && File.Exists(dst) && IsFullJson(dst)) { Skipped++; return; }
        Directory.CreateDirectory(Path.GetDirectoryName(dst)!);
        File.WriteAllText(dst, json);
        Written++;
    }

    // FModel's "Save Properties" writes [{"Type": …}]; its mesh export overwrites material JSONs with a
    // flat format without "Type" (and without the parent) that the add-on can't follow
    static bool IsFullJson(string path)
    {
        try
        {
            using var r = new StreamReader(path);
            var head = new char[400];
            int len = r.Read(head, 0, head.Length);
            var s = new string(head, 0, len).TrimStart();
            return s.StartsWith("[") && s.Contains("\"Type\"");
        }
        catch { return false; }
    }

    bool AssetExists(GameFile f)
    {
        var dir = Path.GetDirectoryName(OutPath(f, ""))!;
        if (!Directory.Exists(dir)) return false;
        var name = f.NameWithoutExtension;
        return File.Exists(Path.Combine(dir, name + ".psk")) || File.Exists(Path.Combine(dir, name + ".pskx"))
            || File.Exists(Path.Combine(dir, name + ".png")) || File.Exists(Path.Combine(dir, name + "_LAYER0.png"));
    }

    void Fail(string path, Exception? e)
    {
        // proxy meshes without geometry (heads' _Wraps cloth proxies): nothing to export, FModel can't either
        if (e?.Message.Contains("has no LODs") == true) { Skipped++; return; }
        Failed++;
        Program.Emit(new { @event = "warning", message = $"{path}: {e?.Message ?? "failed"}" });
    }
}
