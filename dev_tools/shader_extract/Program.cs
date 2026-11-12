// ShaderExtract: dump materials' compiled pixel shaders (DX11 / SM5) from the game's paks.
// Read-only, offline. Settings via environment variables (nothing secret is stored in files):
//   FT_GAME_PAKS   <game>/Discovery/Content/Paks
//   FT_AES         the game's AES key (as in FModel)
//   FT_USMAP       mappings file (.usmap, as in FModel)
//   FT_FMODEL_DATA FModel's .data folder (oodle / zlib dlls)
// Usage: dotnet run -- <out dir> <material package path> [<material package path> ...]
//        dotnet run -- <out dir> --probe            (dump the shader libraries)
//        dotnet run -- <out dir> --dump <folder>... (packages' JSON in FModel's format, see Dump.cs)
// Per material, <out dir>/<name>/ gets: every pixel shader (.dxbc / .asm), base_pass.asm (the largest
// pixel shader = the base pass that writes the G-buffer), shaders.txt, material_layout.json (the uniform
// expression set: parameters, preshaders, texture parameters; TextureIndex values index the master's
// CachedExpressionData.ReferencedTextures, read from FModel's JSON export).
using System.Text;
using CUE4Parse.Compression;
using CUE4Parse.Encryption.Aes;
using CUE4Parse.FileProvider;
using CUE4Parse.MappingsProvider.Usmap;
using CUE4Parse.UE4.Assets.Exports.Material;
using CUE4Parse.UE4.IO;
using CUE4Parse.UE4.IO.Objects;
using CUE4Parse.UE4.Objects.Core.Misc;
using CUE4Parse.UE4.Readers;
using CUE4Parse.UE4.Versions;
using CUE4Parse.Utils;

string Env(string n) => Environment.GetEnvironmentVariable(n) ?? throw new Exception($"set {n}");
if (args.Length < 2) { Console.WriteLine("usage: <out dir> <material path>..."); return; }
var outRoot = args[0];
Directory.CreateDirectory(outRoot);

var data = Env("FT_FMODEL_DATA");
OodleHelper.Initialize(Directory.GetFiles(data, "oodle*.dll").Concat(Directory.GetFiles(data, "oo2*.dll")).First());
var zl = Directory.GetFiles(Path.GetDirectoryName(data)!, "zlib*.dll", SearchOption.AllDirectories).FirstOrDefault();
if (zl != null) ZlibHelper.Initialize(zl);
var provider = new DefaultFileProvider(Env("FT_GAME_PAKS"), SearchOption.TopDirectoryOnly, new VersionContainer((EGame)84344838));
provider.ReadShaderMaps = true;
provider.MappingsContainer = new FileUsmapTypeMappingsProvider(Env("FT_USMAP"));
provider.Initialize();
provider.SubmitKey(new FGuid(), new FAesKey(Env("FT_AES")));
provider.PostMount();

if (args[1] == "--probe") { Probe.Libraries(provider, outRoot); return; }
if (args[1] == "--dump") { Dump.Run(provider, outRoot, args.Skip(2).ToArray()); return; }

// names: FHashedName = CityHash64 of the upper-case UTF-16 name (guess; doesn't match yet)
ulong H(string s) => CityHash.CityHash64(Encoding.Unicode.GetBytes(s.ToUpperInvariant()));
var known = new Dictionary<ulong, string>();
foreach (var n in File.ReadAllLines(Path.Combine(AppContext.BaseDirectory, "names.txt")).Select(l => l.Trim()).Where(l => l.Length > 0 && !l.StartsWith("#")))
    known[H(n)] = n;
string Name(ulong h) => known.TryGetValue(h, out var n) ? n : $"?{h:X16}";

// The SM5 shader libraries. Their index is parsed here (CUE4Parse's FIoStoreShaderCodeArchive reader
// overflows on this game's files; the layout itself is standard):
//   u32 version; then arrays (u32 count + items): ShaderMapHashes (20 B), ShaderHashes (20 B),
//   ShaderGroupIoHashes (FIoChunkId, 12 B), ShaderMapEntries (offset u32, count u32),
//   ShaderEntries (u64: frequency 4 bits, group 30 bits, offset in group 27 bits),
//   ShaderGroupEntries (indices offset, count, uncompressed size, compressed size), ShaderIndices (u32).
var libs = provider.Files.Keys.Where(k => k.Contains("ShaderArchive-") && k.EndsWith("PCD3D_SM5.ushaderbytecode"))
    .Select(k => (name: k, lib: Lib.Parse(provider.SaveAsset(k)))).ToList();
var readers = provider.MountedVfs.OfType<IoStoreReader>().ToList();
var fxc = Directory.GetFiles(@"C:\Program Files (x86)\Windows Kits\10\bin", "fxc.exe", SearchOption.AllDirectories)
    .Where(p => p.Contains("x64")).OrderBy(p => p).Last();
var json = new Newtonsoft.Json.JsonSerializerSettings
    { ReferenceLoopHandling = Newtonsoft.Json.ReferenceLoopHandling.Ignore, Formatting = Newtonsoft.Json.Formatting.Indented };

foreach (var matPath in args.Skip(1))
{
    try { Extract(matPath); }
    catch (Exception e) { Console.WriteLine($"{matPath}: FAILED {e.Message}"); }
}

void Extract(string matPath)
{
    var mat = provider.LoadPackageObject<UMaterialInterface>(matPath);
    // material instances without static-switch changes have no shader map of their own: use the nearest
    // parent's (the output folder keeps the instance's name; its parameter values come from its own JSON)
    var src = mat;
    List<FMaterialResource> res;
    while (true)
    {
        res = (src.LoadedMaterialResources ?? new()).Where(r => r.LoadedShaderMap?.ShaderPlatform == EShaderPlatform.SP_PCD3D_SM5).ToList();
        if (res.Count > 0) break;
        var parent = (src as UMaterialInstance)?.Parent?.Load<UMaterialInterface>();
        if (parent == null) { Console.WriteLine($"{mat.Name}: no SM5 shader map in its chain"); return; }
        src = parent;
    }
    if (src != mat) Console.WriteLine($"{mat.Name}: using the shader map of {src.Name}");
    var sm = (res.FirstOrDefault(r => r.LoadedShaderMap!.ShaderMapId?.QualityLevel.ToString() == "Num") ?? res.First()).LoadedShaderMap!;
    var content = (FMaterialShaderMapContent)sm.Content!;
    var outDir = Path.Combine(outRoot, mat.Name);
    Directory.CreateDirectory(outDir);

    var want = Convert.FromHexString(sm.ResourceHash.ToString()!);
    Lib? lib = null; int mapIndex = -1; string libName = "";
    foreach (var (name, l) in libs)
    {
        var i = l.MapHashes.FindIndex(h => h.AsSpan().SequenceEqual(want));
        if (i >= 0) { lib = l; mapIndex = i; libName = name; break; }
    }
    if (lib == null) { Console.WriteLine($"{mat.Name}: shader map {sm.ResourceHash} not in any SM5 library"); return; }
    var (mapOff, mapCount) = lib.MapEntries[mapIndex];
    var indices = lib.Indices.Skip((int)mapOff).Take((int)mapCount).ToArray();

    var groupCache = new Dictionary<uint, byte[]>();
    byte[] Group(uint g)
    {
        if (groupCache.TryGetValue(g, out var b)) return b;
        var (_, _, usize, csize) = lib.Groups[(int)g];
        var id = new FByteArchive("chunk", lib.GroupChunkIds[(int)g]).Read<FIoChunkId>();
        var raw = readers.First(r => r.DoesChunkExist(id)).Read(id);
        if (csize != usize)
        {
            var dst = new byte[usize];
            OodleHelper.Decompress(raw, 0, raw.Length, dst, 0, dst.Length, null!);
            raw = dst;
        }
        return groupCache[g] = raw;
    }
    byte[] Code(uint shaderIndex)
    {
        var (_, g, off) = lib.Shader(shaderIndex);
        var buf = Group(g);
        var (gOff, gCount, _, _) = lib.Groups[(int)g];
        var end = lib.Indices.Skip((int)gOff).Take((int)gCount).Select(i => lib.Shader(i).off)
            .Where(o => o > off).DefaultIfEmpty((uint)buf.Length).Min();
        return buf[(int)off..(int)end];
    }

    var report = new StringBuilder();
    string? biggest = null; long biggestSize = 0, biggestScore = long.MinValue;
    void Dump(string vf, FShader s)
    {
        var type = Name(s.Type.Hash);
        var code = Code(indices[s.ResourceIndex]);
        var at = code.AsSpan().IndexOf("DXBC"u8);
        report.AppendLine($"{vf,-20} {type,-20} {s.Target.Frequency,-10} bytes {code.Length,6}");
        var freq = s.Target.Frequency.ToString();
        if ((freq != "SF_Pixel" && freq != "SF_Vertex") || at < 0) return;
        // the DXBC container states its own size; Unreal appends optional data after it, which fxc rejects
        var size = BitConverter.ToInt32(code, at + 24);
        var stem = Path.Combine(outDir, $"{(freq == "SF_Vertex" ? "VS_" : "")}{vf}__{type}".Replace('?', '_'));
        File.WriteAllBytes(stem + ".dxbc", code[at..(at + size)]);
        var pr = System.Diagnostics.Process.Start(new System.Diagnostics.ProcessStartInfo(fxc,
            $"/nologo /dumpbin \"{stem}.dxbc\" /Fc \"{stem}.asm\"") { RedirectStandardOutput = true, RedirectStandardError = true })!;
        pr.WaitForExit();
        if (pr.ExitCode != 0) { report.AppendLine("   fxc failed: " + pr.StandardError.ReadToEnd().Trim()); return; }
        if (freq == "SF_Vertex") return;
        // base pass = the pixel shader that writes the G-buffer (o3 = base colour), preferring
        // vertex factories without per-object structured buffers (GPU-scene variants shift every texture
        // register; graphic skins only have those), then the largest
        var asm = File.ReadAllText(stem + ".asm");
        var gbuffer = asm.Contains("dcl_output o3");
        var structured = System.Text.RegularExpressions.Regex.Matches(asm, "dcl_resource_structured").Count;
        var score = (gbuffer ? 1L << 40 : 0) - ((long)structured << 32) + size;
        if (score > biggestScore) { biggestScore = score; biggestSize = size; biggest = stem + ".asm"; }
    }
    foreach (var s in content.Shaders ?? Array.Empty<FShader>()) Dump("material", s);
    foreach (var m in content.OrderedMeshShaderMaps ?? Array.Empty<FMeshMaterialShaderMap>())
        foreach (var s in m.Shaders ?? Array.Empty<FShader>()) Dump(Name(m.VertexFactoryTypeName.Hash), s);
    if (biggest != null) File.Copy(biggest, Path.Combine(outDir, "base_pass.asm"), true);
    File.WriteAllText(Path.Combine(outDir, "shaders.txt"), report.ToString());
    File.WriteAllText(Path.Combine(outDir, "material_layout.json"),
        Newtonsoft.Json.JsonConvert.SerializeObject(content.MaterialCompilationOutput?.UniformExpressionSet, json));
    Console.WriteLine($"{mat.Name}: {indices.Length} shaders from {libName.Split('/').Last()}, base pass {biggestSize} bytes");
}

sealed class Lib
{
    public List<byte[]> MapHashes = new();
    public List<byte[]> GroupChunkIds = new();
    public List<(uint off, uint count)> MapEntries = new();
    public List<ulong> Shaders = new();
    public List<(uint off, uint count, uint usize, uint csize)> Groups = new();
    public List<uint> Indices = new();

    public (uint freq, uint group, uint off) Shader(uint i)
    {
        var p = Shaders[(int)i];
        return ((uint)(p & 0xF), (uint)((p >> 4) & 0x3FFFFFFF), (uint)((p >> 34) & 0x7FFFFFF));
    }

    public static Lib Parse(byte[] b)
    {
        var r = new BinaryReader(new MemoryStream(b));
        var lib = new Lib();
        if (r.ReadUInt32() != 1) throw new Exception("unknown shader library version");
        for (int n = r.ReadInt32(), i = 0; i < n; i++) lib.MapHashes.Add(r.ReadBytes(20));
        r.ReadBytes(20 * r.ReadInt32());
        for (int n = r.ReadInt32(), i = 0; i < n; i++) lib.GroupChunkIds.Add(r.ReadBytes(12));
        for (int n = r.ReadInt32(), i = 0; i < n; i++) lib.MapEntries.Add((r.ReadUInt32(), r.ReadUInt32()));
        for (int n = r.ReadInt32(), i = 0; i < n; i++) lib.Shaders.Add(r.ReadUInt64());
        for (int n = r.ReadInt32(), i = 0; i < n; i++) lib.Groups.Add((r.ReadUInt32(), r.ReadUInt32(), r.ReadUInt32(), r.ReadUInt32()));
        for (int n = r.ReadInt32(), i = 0; i < n; i++) lib.Indices.Add(r.ReadUInt32());
        return lib;
    }
}
