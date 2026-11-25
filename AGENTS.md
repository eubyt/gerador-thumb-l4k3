# Thumbnail workflow

When the user asks to create or improve a thumbnail in this project, use `python3 criar_thumb.py`. Reuse the text and context from the conversation. If the context is sufficient and the user leaves the headline open, choose a short one. Use the existing compositor.

1. Read `thumb.config.json` and `expressoes.json`. Choose a streamer expression for the video. The original photo is `ref/desempregado.png`. For new expressions, use the real frames in `ref/streamer/` as the primary identity references and the AI-edited portrait as a secondary reference. The catalog records their provenance.
2. Prepare the job.

   ```sh
   python3 criar_thumb.py preparar --texto "VIDEO HEADLINE" --contexto "Topic, game, and desired scene" --texto-cartaz "Optional poster subtitle" --slug video-name --expressao surpreso
   ```

   Read `pedido.json`, `prompt.txt`, and `prompt-arte.txt` in the returned system temporary directory. Keep those files out of `output/`.
3. Use the built-in ImageGen tool with the exact contents of `prompt-arte.txt` to generate the lower poster panel. Use `view_image` to inspect local inputs first. The `referencias_imagegen` list contains thumbnail style references from `ref/thumbnails/`. Add any future thumbnail references directly to that folder. Use them for visual style and layout. Keep the streamer, badge, and reference text out of the generated poster. The agent calls ImageGen directly without an SDK or API key.
4. Assemble the thumbnail with the generated poster.

   ```sh
   python3 criar_thumb.py finalizar --job "/path/to/temporary/job" --arte "/path/to/generated-poster.png"
   ```

   The script validates and replaces `output/thumb.png`, clears other output files, removes the temporary job, and prints verification in the terminal.
5. Open `output/thumb.png` and inspect the face, badge, text, accents, margins, and poster. For a cataloged portrait, verify that the terminal reports `rosto_igual_ao_asset_escolhido_redimensionado: true`. If you rebuild the thumbnail, start with `preparar` again.
6. Deliver only the link to `output/thumb.png` at 1080 × 1920. Keep exactly that final PNG in `output/`.

## Visual requirements

- Use a vertical 1080 × 1920 canvas (9:16). Place the streamer at the top, the headline in the middle, and the poster below.
- Use a magenta neon border with a white core, rounded corners, and interior margins.
- Composite the original transparent `assets/badge-streamer.png` beside the upper-left border at (62, 62), with a maximum size of 112 px. Preserve its appearance and proportions. Include its path and URL in the job and prompt.
- Preserve the streamer's identity. Composite the selected portrait at a uniform scale with its face untouched and unobstructed. The original photo is `ref/desempregado.png`. The happy, surprised, sad, and moved variants in `assets/` are transparent PNGs edited with AI. Treat the photos, variants, and badge as read-only inputs.
- The `area_rosto` setting protects the original face region. The catalog's `area_rosto_relativa` protects the variants.
- Have the compositor set the headline in a white, heavy condensed font with the correct accents, a pink outline and glow, and a dark shadow.
- Match the poster to the current video topic. Keep the entire poster, its text, and the top of the streamer's head visible. Give the poster its own copy and use dedicated poster artwork.
- Install dependencies in the computer's Python with `python3 -m pip install --user -r requirements.txt`. If a font is missing, update `fontes` in `thumb.config.json`.

## Choosing an expression

Use the user's requested expression when supplied. Otherwise choose one that fits the video's meaning, including negation and irony. Pass it explicitly with `--expressao`.

- `neutro` for analysis, explanations, and factual content. Uses `ref/desempregado.png`.
- `feliz` for wins, achievements, humor, and celebrations.
- `surpreso` for aliens, mysteries, discoveries, and revelations.
- `triste` for loss, farewells, and longing.
- `emocionado` for reunions, tributes, gratitude, and overcoming adversity.

The selected expression appears in the job and prompts. Match the scene and poster to its mood. The compositor supplies the portrait from the existing PNG. Run `python3 criar_thumb.py expressoes` to list the catalog. The script's `auto` default uses local keyword rules. Use `--foto` to keep the expression in a custom photo.

## Verification after workflow changes

Run `python3 -B -m unittest discover -s tests -v`. The tests use synthetic poster art to verify the compositor. Inspect the actual generated artwork in the final thumbnail before delivery.
