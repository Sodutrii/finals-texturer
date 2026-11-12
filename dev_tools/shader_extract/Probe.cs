// Probe: print size and first bytes of every shader library (run with: dotnet run -- --probe <out dir>)
using CUE4Parse.FileProvider;

static class Probe
{
    public static void Libraries(DefaultFileProvider provider, string outDir)
    {
        foreach (var l in provider.Files.Keys.Where(k => k.EndsWith(".ushaderbytecode")).OrderBy(k => k))
        {
            var b = provider.SaveAsset(l);
            Console.WriteLine($"{l}  {b.Length} bytes");
            Console.WriteLine("   " + BitConverter.ToString(b, 0, Math.Min(64, b.Length)));
            File.WriteAllBytes(Path.Combine(outDir, Path.GetFileName(l)), b);
        }
    }
}
