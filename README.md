# Finals Texturer

A Blender add-on that imports cosmetics and objects from **THE FINALS** and rebuilds their materials in
Blender: clothing, faces and bodies, weapons with their skins, and game objects such as cashout stations,
death statues and props.

Most of the game's shaders were rebuilt from the game's own compiled shaders and checked pixel by pixel
against them, so items look the way they do in the store, not like flat texture dumps.

> Fan project, not affiliated with or endorsed by Embark Studios. All game assets belong to Embark Studios.
> Use what you import for personal projects, renders and fan art, and don't redistribute the game's files.

---

## Contents

- [What it does](#what-it-does)
- [Requirements](#requirements)
- [Installation](#installation)
- [First-time setup](#first-time-setup)
- [Using the add-on](#using-the-add-on)
  - [Panel layout](#panel-layout)
  - [Getting game files: Export Now](#getting-game-files-export-now)
  - [Finding items: search and store images](#finding-items-search-and-store-images)
  - [Character tab](#character-tab)
  - [Clothing tab](#clothing-tab)
  - [Weapons tab](#weapons-tab)
  - [Objects tab](#objects-tab)
  - [Rigging tab](#rigging-tab)
- [Preferences](#preferences)
- [Troubleshooting](#troubleshooting)
- [Known limitations](#known-limitations)
- [For developers](#for-developers)
- [Credits and licences](#credits-and-licences)

---

## What it does

| Tab | What you get |
|---|---|
| **Character** | Every face, on the Light, Medium or Heavy body, with skin tone, eyes, teeth, eyelashes, brows and body-type shape keys |
| **Clothing** | Every clothing item and outfit in every skin, fitted to the chosen body class, with the game's layered materials (colour schemes, patterns, wear, decals) |
| **Weapons** | Every weapon assembled from its parts, with any of its skins, including the rank skins (Gold, Platinum, Diamond, Ruby, Emerald) |
| **Objects** | Cashout objectives (cashbox, vault, cashout station), death statues in every team colour, arena carryables, gameplay objects (jump pads, ziplines, ladders, cannon…) and about 440 props you can pick up or break |
| **Rigging** | Simplifies the game's body rig, attaches clothing to it, and can build an Auto-Rig Pro control rig |

It also covers:

- **Real names.** Items are listed by their in-game names, and you can search with typos.
- **Store images.** You can see what an item looks like before importing it.
- **No FModel clicking.** Missing game files can be exported straight from the add-on with **Export Now**.

---

## Requirements

- **Blender 4.2 or newer.** Developed and tested on Blender 5.1.
- **[FModel](https://fmodel.app/), set up for THE FINALS.** The game must load in FModel with its AES key and
  mappings file (`.usmap`). The add-on reads FModel's settings but never stores or prints the key.
- **The PSK importer add-on** ([io_scene_psk_psa](https://github.com/DarklightGames/io_scene_psk_psa)), which
  imports the game's meshes. Install and enable it in Blender before using Finals Texturer.
- **For Export Now (recommended):** Windows and the **.NET 10 runtime**. Current FModel versions need .NET 10
  as well, so if FModel runs, Export Now can run too. On other systems you can still export with FModel by
  hand (see [Copy Path](#getting-game-files-export-now)).
- **Optional:** [Auto-Rig Pro](https://blendermarket.com/products/auto-rig-pro) 3.77.43+ for the control-rig
  button in the Rigging tab.

---

## Installation

1. Download the latest `finals_texturer-<version>.zip` from the
   [Releases page](https://github.com/Sodutrii/finals-texturer/releases). Don't unzip it.
2. In Blender: **Edit → Preferences → Get Extensions**, open the drop-down menu (top right) and choose
   **Install from Disk…**, then pick the zip.
3. Make sure the PSK importer add-on is installed and enabled too.

To update, install the newer zip the same way. Your settings are kept.

---

## First-time setup

1. Open the sidebar in the 3D Viewport (**N**) and go to the **Finals** tab.
2. In **Edit → Preferences → Add-ons → Finals Texturer**, set **FModel Export Folder** to FModel's
   `Output\Exports` folder (the folder that contains `Discovery`).
3. In any tab, click **Check Setup**. It shows whether the add-on found FModel, your game files and the
   Export Now helper, and what to fix if not.

That's it. You don't need to export anything from FModel up front: the add-on lists every item in the game
(from a name list shipped with it) and fetches what's missing when you want it.

---

## Using the add-on

### Panel layout

Every tab has the same layout, top to bottom:

1. **Search box**, plus the **store image** button next to it.
2. **Drop-downs** to pick the item, for example category → item → skin.
3. **Store image** of the selected item.
4. **Status box**: whether everything the item needs has been exported, with **Export Now** if not.
5. **Import / Apply** buttons.
6. **Options** fold-out.
7. Footer: **Show Log**, **Rescan**, **Check Setup**.

Icons next to names show the export status:

- ✔ means fully exported.
- A warning icon means some textures are missing.
- An empty icon means not exported yet.

### Getting game files: Export Now

When the status box says something is missing, click **Export Now**. The bundled helper reads the game files
directly, using FModel's own setup, and writes exactly what the item needs (meshes, materials, textures,
names) into your FModel Export Folder. FModel doesn't have to be open.

- It typically takes 2–7 seconds per item. You can cancel at any time.
- The output is identical to FModel's own exports, so you can mix both freely.
- If Export Now isn't available (macOS/Linux, or FModel not set up), use **Copy Path** instead. It copies the
  item's folder path, which you paste into FModel's search and export there.

### Finding items: search and store images

- **Search** works by in-game name, skin name or file name, and tolerates typos.
- **Don't know the name?** Search for a word that describes it (e.g. `beanie`, `jacket`, `sword`), then click
  the **image button** next to the search box. A popup shows the store images of all results, 20 per page.
  Click a name to select that item.
  - Images that aren't exported yet are fetched automatically into a small cache. They don't go into your
    Exports folder.
  - The cache is limited to 200 MB, and the least recently viewed images are removed first. You can clear it
    in Preferences.
  - Objects have no store images, so the button is greyed out there.

### Character tab

- Pick a **face** and a **body class** (Light, Medium, Heavy), plus gender and body type in Options.
- **Import Character** brings in the body and head. The head is fitted to the class and follows the body's
  skeleton.
- **Update Selected** gives an imported character the chosen face and body type. To change the class,
  import again.

### Clothing tab

- Pick **category → item → skin**, or search. Outfits import every piece at once with **Import Outfit**.
- **Import** fits the item to the body class you chose. Items made for another class are scaled the way the
  game does it, and hats sit on the head.
- **Apply to Selected** puts the chosen skin's materials on items you already imported.
- **Number decals** (jerseys etc.): the game sets the number per player, so you choose it in the panel.

### Weapons tab

- Pick a **weapon** and a **skin**. **Import Weapon** assembles the base and its parts (magazine, sights,
  barrel…) at the right sockets.
- **Apply Skin** switches the skin on an already imported weapon.
- The five **rank skins** are listed right after the default skin as **Rank: Gold**, **Rank: Platinum**,
  **Rank: Diamond**, **Rank: Ruby** and **Rank: Emerald**.

### Objects tab

- Categories: **Cashout**, **Death Statues**, **Arena Carryables**, **Gameplay Objects** and **Props**
  (grouped by location).
- Objects import in their **default look**, assembled from all their parts under one Empty.
- Death statues have a **Team colour** option (blue, orange, pink, purple, red, indigo, lime). It also
  recolours statues you've selected.

### Rigging tab

Based on zebby's rig tools.

1. **Simplify Body Rig** merges the game's helper bones (twist, corrective) into a clean skeleton.
2. **Attach Selected Pieces** binds imported clothing to that skeleton. Hats and other socket items follow
   their bone.
3. **Generate ARP Control Rig** (needs Auto-Rig Pro) builds animation controls that drive the simplified
   skeleton.

---

## Preferences

**Edit → Preferences → Add-ons → Finals Texturer**:

| Setting | What it does |
|---|---|
| **FModel Export Folder** | FModel's `Output\Exports` folder. Imports read from here, and Export Now writes here |
| **Import Size** | **Blender Adjusted Scale** (default): real-world size, 1 unit = 1 m. **Ingame Scale**: the game's numbers, 1 unit = 1 cm, which looks 100× too big in a default Blender scene |
| **Store image cache** | Size of the store-image cache (max 200 MB), with a **Clear Cache** button |

Import Size is also in each tab's Options fold-out.

---

## Troubleshooting

| Problem | What to do |
|---|---|
| "The PSK importer isn't available" | Install and enable the io_scene_psk_psa add-on |
| Export Now is greyed out or reports a problem | Click **Check Setup**. FModel must be set up for THE FINALS (game folder, AES key, mappings), and the .NET 10 runtime installed |
| "Material files need re-saving" | FModel's mesh export overwrote some material files with a reduced format. Click **Export Now**, which replaces them, or in FModel run **Save Properties (.json)** on that folder |
| Items show as "not exported" after exporting in FModel | Click **Rescan** |
| Something looks wrong | Click **Show Log** for warnings about missing files. If it's a material problem, please open an issue with the item name and a screenshot next to its store image |
| Imported model is huge | Import Size is set to Ingame Scale. Switch to Blender Adjusted Scale |
| After a game update, new items are missing from the lists | The shipped name list is built from the game files. A new add-on release adds the new items |

---

## Known limitations

- Materials are rebuilt for Blender's renderer, so effects that only exist in the game engine are
  approximated or left out: animated effects (shimmer, panning patterns, glitches), parallax depth, damage
  states, weather and wetness.
- **Approximate looks:**
  - the Diamond, Ruby and Emerald rank skins (no inner depth or sparkle);
  - some special weapon effect skins;
  - props using shared building materials;
  - eyes (no refraction).
- **Not built yet** (these fall back to a generic material):
  - nails, LED screens, fur, holograms;
  - a few special faces (hard-surface, space, cel-shaded, LED screen);
  - tattoos and body paint.
- Some items have no shop name in the game files and show a placeholder in brackets.
- Zipline cables and full ladders are built by game code at runtime, so only their parts can be imported.
- Gadgets are not included yet.

---

## For developers

### Repository layout

| Path | Contents |
|---|---|
| `finals_texturer/` | The add-on (Blender extension). `builders/` holds the material builders (one per game shader family) |
| `exporter/` | The Export Now helper (`ft_exporter`, C#/.NET 10, built on CUE4Parse) |
| `dev_tools/` | Development scripts: comparison renders against store images, regression lists, and `shader_extract/`, the compiled-shader extraction and simulation pipeline used to verify materials |
| `add-on-io-scene-psk-psa-v9_1_2/` | Copy of the PSK importer used by the test scripts |
| `CLAUDE.md` | Detailed project notes: verified shader rules, code map, workflows |

### Building

The add-on zip (from the repository root, with Blender's command line):

```
blender --factory-startup --command extension build --source-dir finals_texturer --output-dir .
```

The Export Now helper (needs the .NET 10 SDK; the output is not tracked in git):

```
dotnet publish exporter/FtExporter.csproj -c Release -r win-x64 --self-contained false -p:DebugType=none -o finals_texturer/bin/exporter
```

See [`exporter/README.md`](exporter/README.md) for the helper's command line.

### Releases

Pushing a new version to `main` triggers `.github/workflows/release.yml`. It builds the helper, packs
`finals_texturer-<version>.zip` and publishes it as release `v<version>`. Bump the version in **both**
`finals_texturer/blender_manifest.toml` and `bl_info` in `finals_texturer/__init__.py`, or the workflow
fails.

### How the materials are made

Each game master material has a builder that recreates its node graph in Blender. Where possible, the
formula comes from the game's compiled shader:

1. `dev_tools/shader_extract` extracts the compiled pixel shader for a material.
2. It annotates the shader with parameter names and simulates it on the item's textures.
3. `compare_game.py` checks the result texel by texel against the add-on's baked material.

The verified rules for each shader are listed in `CLAUDE.md`. Fixes are kept as general rules that hold for
every skin, not per-item tweaks.

---

## Credits and licences

- **Finals Texturer** is licensed under the GPL-3.0-or-later.
- Game assets © Embark Studios. This project doesn't include or redistribute any game files.
- [FModel](https://fmodel.app/) and [CUE4Parse](https://github.com/FabianFG/CUE4Parse) (Apache 2.0) make
  reading the game files possible; the Export Now helper is built on CUE4Parse. The helper's third-party
  libraries are listed in [`exporter/THIRD_PARTY_NOTICES.txt`](exporter/THIRD_PARTY_NOTICES.txt).
- [io_scene_psk_psa](https://github.com/DarklightGames/io_scene_psk_psa) by DarklightGames imports the
  meshes.
- The Rigging tab is based on **zebby**'s `finals_rig_tools.py`.
- The compiled-shader approach follows the method of **ChronoHax** (author of finals_materials).
