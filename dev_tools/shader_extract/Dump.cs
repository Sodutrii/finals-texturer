// Dump packages' exports as JSON, in FModel's "Save Properties (.json)" format, straight from the game's paks.
// Used to build the complete in-game name library (every DA_ data asset under the given folders) without
// exporting anything by hand.
//   dotnet run -- <out dir> --dump <folder prefix> [<folder prefix> ...] [--names DA_,DA_MetaData_]
// Folder prefixes are game paths as the provider lists them, e.g. Discovery/Content/Discovery/Characters.
// Writes <out dir>/<path>.json mirroring the game folders (same layout as FModel's Output/Exports).
using CUE4Parse.FileProvider;
using Newtonsoft.Json;

static class Dump
{
    public static void Run(IFileProvider provider, string outRoot, string[] args)
    {
        var prefixes = new List<string>();
        var names = new List<string> { "DA_" };
        for (int i = 0; i < args.Length; i++)
        {
            if (args[i] == "--names" && i + 1 < args.Length) { names = args[++i].Split(',').ToList(); continue; }
            prefixes.Add(args[i].Trim('/'));
        }
        var files = provider.Files.Values
            .Where(f => f.Extension == "uasset"
                        && prefixes.Any(p => f.Path.StartsWith(p, StringComparison.OrdinalIgnoreCase))
                        && names.Any(n => f.Name.StartsWith(n, StringComparison.OrdinalIgnoreCase)))
            .OrderBy(f => f.Path).ToList();
        Console.WriteLine($"{files.Count} packages to dump");
        var settings = new JsonSerializerSettings { Formatting = Formatting.Indented,
                                                    ReferenceLoopHandling = ReferenceLoopHandling.Ignore };
        int done = 0, failed = 0;
        foreach (var f in files)
        {
            try
            {
                var pkg = provider.LoadPackage(f);
                var exports = pkg.GetExports().ToList();
                var json = JsonConvert.SerializeObject(exports, settings);
                var dst = Path.Combine(outRoot, Path.ChangeExtension(f.Path, ".json").Replace('/', Path.DirectorySeparatorChar));
                Directory.CreateDirectory(Path.GetDirectoryName(dst)!);
                File.WriteAllText(dst, json);
                done++;
            }
            catch (Exception e)
            {
                failed++;
                if (failed <= 10) Console.WriteLine($"  {f.Path}: {e.Message}");
            }
            if ((done + failed) % 500 == 0) Console.WriteLine($"  {done + failed}/{files.Count}");
        }
        Console.WriteLine($"dumped {done}, failed {failed}");
    }
}
