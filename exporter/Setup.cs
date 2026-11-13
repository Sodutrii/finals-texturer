// Reads FModel's own setup (%APPDATA%\FModel\AppSettings.json + its .data folder), so the exporter needs
// nothing from the user: game folder, AES key, mappings file, engine version, export options, and the
// Oodle / zlib / Detex libraries FModel downloaded. Nothing read here is ever written anywhere.
using Newtonsoft.Json.Linq;

namespace FtExporter;

sealed class Setup
{
    public string SettingsPath = "";
    public string GameDir = "";
    public string PaksDir = "";
    public string AesKey = "";
    public List<(string Guid, string Key)> DynamicKeys = new();
    public string Mappings = "";
    public int UeVersion;
    public int TexturePlatform;
    public int SocketFormat = 1;
    public bool MorphTargets = true;
    public string DataDir = "";
    public string Oodle = "";
    public string Zlib = "";
    public string Detex = "";
    public string FModelExports = "";
    public List<string> Problems = new();

    public static Setup Load(string? settingsOverride = null)
    {
        var s = new Setup();
        s.SettingsPath = settingsOverride ?? Path.Combine(
            Environment.GetFolderPath(Environment.SpecialFolder.ApplicationData), "FModel", "AppSettings.json");
        if (!File.Exists(s.SettingsPath))
        {
            s.Problems.Add("FModel's settings weren't found. Install FModel and open The Finals in it once.");
            return s;
        }
        JObject j;
        try { j = JObject.Parse(File.ReadAllText(s.SettingsPath)); }
        catch (Exception e) { s.Problems.Add($"FModel's settings couldn't be read ({e.Message})."); return s; }

        s.GameDir = (string?)j["GameDirectory"] ?? "";
        var per = j["PerDirectory"] as JObject;
        JObject? game = null;
        if (per != null)
        {
            game = per.Properties().FirstOrDefault(p => SamePath(p.Name, s.GameDir))?.Value as JObject;
            // FModel last used another game: take the folder set up for The Finals
            if (game == null || !IsFinals(game))
            {
                var finals = per.Properties().FirstOrDefault(p => p.Value is JObject o && IsFinals(o) && Directory.Exists(p.Name));
                if (finals != null) { game = (JObject)finals.Value; s.GameDir = finals.Name; }
            }
        }
        if (game == null || !Directory.Exists(s.GameDir))
        {
            s.Problems.Add("FModel has no game folder for The Finals. Open The Finals in FModel once.");
            return s;
        }
        var paks = Path.Combine(s.GameDir, "Discovery", "Content", "Paks");
        s.PaksDir = Directory.Exists(paks) ? paks : s.GameDir;

        s.UeVersion = (int?)game["UeVersion"] ?? 0;
        s.TexturePlatform = (int?)game["TexturePlatform"] ?? 0;
        var aes = game["AesKeys"] as JObject;
        s.AesKey = (string?)aes?["mainKey"] ?? "";
        if (aes?["dynamicKeys"] is JArray dyn)
            foreach (var k in dyn.OfType<JObject>())
                if ((string?)k["guid"] is { } g && (string?)k["key"] is { } kk && kk.Length > 0) s.DynamicKeys.Add((g, kk));
        if (s.AesKey.Length == 0) s.Problems.Add("FModel has no AES key for The Finals.");

        // mappings: the file set in FModel (Settings > Mapping), else the newest one FModel downloaded
        if (game["Endpoints"] is JArray eps)
            foreach (var ep in eps.OfType<JObject>())
                if ((string?)ep["FilePath"] is { } fp && fp.EndsWith(".usmap", StringComparison.OrdinalIgnoreCase) && File.Exists(fp))
                    s.Mappings = fp;

        s.SocketFormat = (int?)j["SocketExportFormat"] ?? 1;
        s.MorphTargets = (bool?)j["SaveMorphTargets"] ?? true;
        var output = (string?)j["OutputDirectory"] ?? "";
        s.FModelExports = (string?)j["ModelDirectory"] ?? (output.Length > 0 ? Path.Combine(output, "Exports") : "");
        s.DataDir = Path.Combine(output, ".data");
        if (Directory.Exists(s.DataDir))
        {
            s.Oodle = First(s.DataDir, "oodle*.dll", "oo2core*.dll");
            s.Zlib = First(s.DataDir, "zlib*.dll");
            s.Detex = First(s.DataDir, "Detex*.dll");
            if (s.Mappings.Length == 0)
            {
                var maps = Path.Combine(s.DataDir, "mappings");
                if (Directory.Exists(maps))
                    s.Mappings = new DirectoryInfo(maps).GetFiles("*.usmap", SearchOption.AllDirectories)
                        .OrderByDescending(f => f.LastWriteTimeUtc).Select(f => f.FullName).FirstOrDefault() ?? "";
            }
        }
        if (s.Oodle.Length == 0) s.Problems.Add("FModel's Oodle library wasn't found. Open The Finals in FModel once (it downloads it).");
        if (s.Mappings.Length == 0) s.Problems.Add("No mappings file (.usmap) is set in FModel for The Finals.");
        return s;
    }

    static bool IsFinals(JObject o) =>
        ((string?)o["GameName"] ?? "").Contains("Finals", StringComparison.OrdinalIgnoreCase);

    static bool SamePath(string a, string b) =>
        string.Equals(Path.GetFullPath(a).TrimEnd('\\', '/'), Path.GetFullPath(b.Length > 0 ? b : ".").TrimEnd('\\', '/'),
                      StringComparison.OrdinalIgnoreCase);

    static string First(string dir, params string[] patterns) =>
        patterns.SelectMany(p => Directory.GetFiles(dir, p)).FirstOrDefault() ?? "";
}
