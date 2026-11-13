# ft_exporter

The helper behind the add-on's **Export Now** button. It reads The Finals' game files with
[CUE4Parse](https://github.com/FabianFG/CUE4Parse) (the library FModel is built on) and writes what an
item needs into the Exports folder, in FModel's layout and format:

- properties as `.json` (FModel's "Save Properties"), meshes as `.psk`/`.pskx`, textures as `.png`
  (texture arrays as `_LAYER<n>.png`), checked file for file against FModel's own exports;
- for each start file: the files next to it (store image, metadata) and everything it references that the
  add-on uses: meshes, materials up to the master, the master's textures, curve atlases, data assets
  (inside the same content category, e.g. `Discovery/Items`).

It needs nothing from the user: game folder, AES key, mappings file (`.usmap`), engine version, export
options and the Oodle / zlib / Detex libraries come from **FModel's own setup**
(`%APPDATA%\FModel\AppSettings.json` and FModel's `.data` folder). The key is never printed or stored.

## Usage

```
ft_exporter check
ft_exporter export --out <Exports folder> [--force] <path>...
```

Paths: `/Game/...` or `Discovery/Content/...` (extension optional); `<folder>/*` = that folder's own files,
`<folder>` = everything under it. Existing meshes/textures and full JSONs are kept unless `--force`; JSONs
flattened by FModel's mesh export are replaced. Output: one JSON object per line (`status`, `scan`,
`progress`, `warning`, `error`, `done`).

## Build

Requires the .NET 10 SDK. The add-on looks for the published files in `finals_texturer/bin/exporter`:

```
dotnet publish exporter/FtExporter.csproj -c Release -r win-x64 --self-contained false -p:DebugType=none -o finals_texturer/bin/exporter
```

The release workflow does this before zipping. It runs on the .NET 10 runtime, which FModel needs too.
After a game update that changes the engine version, update the `CUE4Parse` / `CUE4Parse-Conversion`
package versions in `FtExporter.csproj`.
