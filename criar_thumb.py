#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import itertools
import json
import os
import tempfile
from pathlib import Path
import re
import shutil
import unicodedata
from urllib.request import Request, urlopen

try:
    from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont, ImageOps
except ImportError:
    raise SystemExit("Instale a dependencia: python3 -m pip install --user -r requirements.txt")

ROOT = Path(__file__).resolve().parent
SIZE = (1080, 1920)
PHOTO_ORIGIN = (48, 48)
PHOTO_LIMIT = (984, 565)
HEADLINE_BOX = (72, 636, 1008, 995)
POSTER_BOX = (48, 1012, 1032, 1872)
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
EXPRESSIONS = ("neutro", "feliz", "surpreso", "triste", "emocionado")


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def asset(root: Path, value: str) -> Path:
    return (root / value).resolve()


def validate_badge(path: Path) -> None:
    if not path.is_file() or path.read_bytes()[:8] != PNG_SIGNATURE:
        raise ValueError(f"A badge precisa ser um PNG verdadeiro: {path}")
    with Image.open(path) as im:
        im.load()
        if im.format != "PNG" or "A" not in im.getbands():
            raise ValueError("A badge deve ser PNG com canal alpha; nao renomeie JPEG para PNG.")
        if im.getchannel("A").getextrema()[0] == 255:
            raise ValueError("A badge precisa preservar a transparencia do arquivo original.")


def ensure_badge(root: Path, config: dict) -> Path:
    path = asset(root, config["badge"])
    if not path.exists():
        request = Request(config["badge_url"], headers={"User-Agent": "ShortsThumbnail/1.0"})
        with urlopen(request, timeout=30) as response:
            data = response.read(2_000_001)
        if len(data) > 2_000_000 or not data.startswith(PNG_SIGNATURE):
            raise ValueError("Download da badge nao retornou um PNG valido de ate 2 MB.")
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".download.png")
        try:
            temporary.write_bytes(data)
            validate_badge(temporary)
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)
    validate_badge(path)
    return path


def normalize_text(value: str) -> str:
    lines = [" ".join(line.split()) for line in unicodedata.normalize("NFC", value).splitlines()]
    text = "\n".join(line for line in lines if line).upper()
    if not text:
        raise ValueError("Informe uma frase em --texto.")
    if len(text.split()) > 24 or len(text) > 140:
        raise ValueError("Frase longa demais para uma thumb. Use ate 24 palavras e 140 caracteres.")
    return text


def choose_font(config: dict) -> str:
    for value in config["fontes"]:
        if Path(value).is_file():
            ImageFont.truetype(value, 100)
            return value
    raise ValueError("Nenhuma fonte encontrada. Configure 'fontes' em thumb.config.json.")


def text_layout(text: str, font_path: str) -> dict:
    """Escolhe quebras e tamanho sem esticar letras nem remover acentos."""
    words = text.split()
    candidates = []
    if "\n" in text:
        candidates = [text.splitlines()]
    else:
        for count in range(1, min(3, len(words)) + 1):
            for cuts in itertools.combinations(range(1, len(words)), count - 1):
                edges = (0, *cuts, len(words))
                candidates.append([" ".join(words[a:b]) for a, b in zip(edges, edges[1:])])
    max_width = HEADLINE_BOX[2] - HEADLINE_BOX[0] - 24
    max_height = HEADLINE_BOX[3] - HEADLINE_BOX[1] - 26
    best = None
    for lines in candidates:
        if len(lines) > 3:
            continue
        for size in range(210, 71, -1):
            font = ImageFont.truetype(font_path, size)
            boxes = [font.getbbox(line) for line in lines]
            widths = [b[2] - b[0] for b in boxes]
            heights = [b[3] - b[1] for b in boxes]
            gap = max(12, size // 12)
            height = sum(heights) + gap * (len(lines) - 1)
            if max(widths) <= max_width and height <= max_height:
                balance = min(widths) / max(widths)
                score = (size * (0.8 + 0.2 * balance), size)
                if best is None or score > best[0]:
                    best = (score, {"linhas": lines, "tamanho_fonte": size,
                                   "altura": height, "intervalo": gap})
                break
    if best is None:
        raise ValueError("A frase nao cabe com boa leitura. Reduza o texto ou indique ate 3 linhas.")
    return best[1]


def expression_catalog(root: Path, config: dict) -> dict:
    return load_json(asset(root, config["catalogo_expressoes"]))["expressoes"]


def select_expression(root: Path, config: dict, requested: str, text: str, context: str) -> tuple:
    catalog = expression_catalog(root, config)
    mode = "explicita"
    name = requested
    if requested == "auto":
        # Fallback por palavras-chave; a IA pode escolher explicitamente antes de preparar.
        value = unicodedata.normalize("NFKD", text + " " + context).encode("ascii", "ignore").decode().lower()
        rules = (
            ("emocionado", ("emocion", "homenagem", "reencontro", "superacao", "lagrimas de alegria")),
            ("triste", ("triste", "luto", "saudade", "despedida", "morte", "perda")),
            ("feliz", ("feliz", "vitoria", "conquista", "comemor", "engrac", "hilario")),
            ("surpreso", ("alien", "surpres", "misterio", "revelac", "inacreditavel", "ovni", "chocante")),
        )
        name = next((label for label, terms in rules if any(term in value for term in terms)), "neutro")
        mode = "regra_local"
    if name not in EXPRESSIONS or name not in catalog:
        raise ValueError(f"Expressao desconhecida: {name}. Opcoes: {', '.join(EXPRESSIONS)}.")
    entry = catalog[name]
    path = asset(root, entry["arquivo"])
    if not path.is_file():
        raise ValueError(f"O asset da expressao {name} nao foi encontrado: {path}")
    return name, entry, mode


def photo_spec(path: Path, config: dict, custom: bool) -> dict:
    with Image.open(path) as im:
        im.load()
        width, height = im.size
    crop = [0, 0, width, height] if custom or not config.get("recorte_foto") else config["recorte_foto"]
    left, top, right, bottom = crop
    if not (0 <= left < right <= width and 0 <= top < bottom <= height):
        raise ValueError(f"Recorte da foto fora dos limites {width}x{height}.")
    face = None if custom else config.get("area_rosto")
    if not custom and face is None and config.get("area_rosto_relativa"):
        rel = config["area_rosto_relativa"]
        face = [round(rel[0]*width), round(rel[1]*height), round(rel[2]*width), round(rel[3]*height)]
    if face and not (left <= face[0] < face[2] <= right and top <= face[1] < face[3] <= bottom):
        raise ValueError("O recorte removeria parte da area protegida do rosto.")
    return {"caminho": str(path), "sha256": sha256(path), "recorte": crop, "area_rosto": face}


def prompt_art(spec: dict) -> str:
    return f"""Use case: ads-marketing / stylized-concept.
Asset: ONLY the lower illustrated poster panel for a YouTube Shorts thumbnail.
Output: landscape-near-square 1152x1024, aspect ratio about 1.125:1, no phone mockup.
Video context supplied by the user:
{spec["contexto"]}

Create one visually striking physical gaming poster, almost straight on, attached to a dark teal tiled wall.
The paper occupies about 92% of the image; keep its entire silhouette inside the canvas.
Turn the context into ONE clear dramatic scene, large shapes and strong storytelling at phone size.
Premium cinematic game key art, tactile paper, restrained worn corners, sharp artwork.
Palette: magenta and violet, rich near-black shadows, selective cyan or alien-green accents when appropriate.
Respect the game's recognizable identity when specified in the context.
When the context is GTA VI and aliens: prominent recognizable stacked Grand Theft Auto VI branding,
Vice City/Leonida palms and skyline, one alien face with deep black eyes, a flying saucer with a green beam,
purple tropical dusk. The image illustrates the video's theme; do not claim an official announcement.
Poster text, verbatim: {json.dumps(spec["texto_cartaz"], ensure_ascii=False)}.
If that value is empty, use no supporting slogan. No extra copy, no invented unreadable microtext.
Keep all typography and important visual details at least 6% away from canvas edges.
The main headline {json.dumps(spec["texto"], ensure_ascii=False)} will be added by Python ABOVE this image;
DO NOT render that headline in the poster.
Reference images are composition/palette references only. No people from those references.

Selected streamer expression: {spec["expressao"]["nome"]}.
Expression mood: {spec["expressao"]["descricao"]}.
Make the scenario and visual storytelling coherent with the selected expression and video context.
The selected streamer asset will be composed above this panel by Python; DO NOT regenerate his face.
Final composition contract, for context only: selected streamer asset above, headline in the middle,
this poster below, a continuous neon magenta/white border.
Mandatory real PNG badge: {spec["badge"]["caminho"]}
Original badge URL: {spec["badge"]["url"]}
The Python compositor places that EXACT transparent PNG at the upper-left corner beside the border,
at {spec["badge"]["posicao"]}, contained in {spec["badge"]["tamanho"]}x{spec["badge"]["tamanho"]} pixels.
DO NOT draw or recreate the badge, the streamer, the headline or the outer neon frame in this panel.
No generic substitute badge, no invented LK logo, no facial reconstruction.
No QR code, watermark, YouTube controls, mockups, duplicated poster or extra panels.
"""


def full_prompt(spec: dict, art_prompt: str) -> str:
    return f"""# Briefing padronizado da thumb

Texto central EXATO: {spec["texto"]}
Contexto: {spec["contexto"]}
Canvas: 1080 x 1920, vertical 9:16.
Leia AGENTS.md e execute criar_thumb.py; nao improvise outro compositor.

1. Parte superior: asset escolhido de {spec["foto"]["caminho"]}.
   Expressao: {spec["expressao"]["nome"]} — {spec["expressao"]["descricao"]}.
   Expressao previamente editada por IA: {spec["foto"]["expressao_editada_por_IA"]}.
   A foto neutra e original; as outras expressoes sao variantes autorizadas e editadas por IA.
   Recorte configurado: {spec["foto"]["recorte"]}; escala uniforme.
   Nao regenerar, retocar, suavizar, deformar, recolorir ou alterar o rosto, a expressao ou o cabelo.
   O asset escolhido entra diretamente por composicao Python, sem novas alteracoes no rosto.
   Nesta etapa a IA gera apenas a arte do cartaz inferior, coerente com a expressao escolhida.
2. Centro: frase exata, com acentos, branca, fonte pesada/condensada,
   contorno e brilho magenta, sombra escura; quebra automatica em ate 3 linhas.
3. Embaixo: um cartaz relacionado ao contexto, sem repetir a frase central.
4. Moldura neon magenta com nucleo branco, continua, arredondada e dentro do canvas.
5. BADGE OBRIGATORIA: {spec["badge"]["caminho"]}.
   PNG original com transparencia, baixado de {spec["badge"]["url"]}.
   Posicao padrao: canto superior esquerdo ao lado da borda, x/y={spec["badge"]["posicao"]},
   tamanho maximo {spec["badge"]["tamanho"]} px, escala proporcional.
   Compor o arquivo PNG verdadeiro; nunca redesenhar ou substituir por um logo gerado.
6. Referencias de layout: {", ".join(spec["referencias"]) or "nenhuma"}.
7. Finalizar com o script; a unica saida persistente e output/thumb.png.
   Prompts e pedido ficam em pasta temporaria do sistema, removida depois da finalizacao.
   A verificacao e exibida no terminal. Nao salvar JPG, preview, JSON ou copias em output.
   Conferir visualmente o PNG final, acentos, badge, rosto e margens.
   A nova thumb substitui a anterior somente apos a validacao.
   O script nao chama a IA nem exige chave de API; o agente chama ImageGen entre as etapas.

# Prompt para a ferramenta de imagens

{art_prompt}
"""


def prepare(args: argparse.Namespace, root: Path = ROOT) -> Path:
    config = load_json(root / "thumb.config.json")
    if tuple(config["tamanho"]) != SIZE:
        raise ValueError("Este template foi definido para 1080x1920.")
    text = normalize_text(args.texto)
    context = args.contexto.strip()
    if not context:
        raise ValueError("Informe o tema do video em --contexto.")
    font = choose_font(config)
    layout = text_layout(text, font)
    requested = getattr(args, "expressao", "auto")
    if args.foto:
        if requested not in ("auto", "neutro"):
            raise ValueError("Use --foto ou uma expressao do catalogo; nao misture as duas entradas.")
        photo_path = asset(root, args.foto)
        info = {"nome": "personalizada", "descricao": "Expressao da foto personalizada",
                "selecao": "foto_personalizada"}
        edited = False
        settings = config
    else:
        name, entry, mode = select_expression(root, config, requested, text, context)
        photo_path = asset(root, entry["arquivo"])
        info = {"nome": name, "descricao": entry["descricao"], "selecao": mode}
        edited = entry["gerada_por_IA"]
        settings = dict(config)
        if name != "neutro":
            settings.update(recorte_foto=None, area_rosto=entry.get("area_rosto"),
                            area_rosto_relativa=entry.get("area_rosto_relativa"))
    if not photo_path.is_file():
        raise ValueError(f"Foto nao encontrada: {photo_path}")
    photo = photo_spec(photo_path, settings, bool(args.foto))
    photo["expressao_editada_por_IA"] = edited
    badge = ensure_badge(root, config)
    slug = args.slug
    if slug is None:
        slug = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()
        slug = re.sub(r"[^a-z0-9]+", "-", slug).strip("-")[:64] or "thumb"
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,79}", slug):
        raise ValueError("Use um slug com letras minusculas ASCII, numeros, hifen ou sublinhado.")
    job = Path(tempfile.mkdtemp(prefix=f"shorts-thumb-{slug}-")).resolve()
    (job / ".shorts-thumb-job").write_text(str(root.resolve()), encoding="utf-8")
    refs_dir = asset(root, config["referencias"])
    refs = sorted(str(p.resolve()) for p in refs_dir.iterdir()
                  if p.is_file() and p.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"}
                  and p.resolve() not in {asset(root, config["foto"]), photo_path}) if refs_dir.is_dir() else []
    spec = {"versao": 3, "expressao": info, "raiz_projeto": str(root.resolve()), "texto": text, "contexto": context, "texto_cartaz": args.texto_cartaz.strip(),
            "tamanho": list(SIZE), "foto": photo, "fonte": font, "layout_texto": layout,
            "badge": {"caminho": str(badge), "sha256": sha256(badge), "url": config["badge_url"],
                      "posicao": config["badge_posicao"], "tamanho": config["badge_tamanho"]},
            "referencias": refs, "pipeline": "ImageGen para o cartaz + Python/Pillow para a composicao"}
    art_prompt = prompt_art(spec)
    write_json(job / "pedido.json", spec)
    (job / "prompt-arte.txt").write_text(art_prompt, encoding="utf-8")
    (job / "prompt.txt").write_text(full_prompt(spec, art_prompt), encoding="utf-8")
    print(json.dumps({"job": str(job.resolve()), "prompt": str((job / "prompt.txt").resolve()),
                      "prompt_imagegen": str((job / "prompt-arte.txt").resolve()),
                      "referencias_imagegen": refs, "expressao": info,
                      "proximo_passo": "Gerar SOMENTE o cartaz com ImageGen e depois executar finalizar.",
                      "finalizar": f'python3 criar_thumb.py finalizar --job "{job}" --arte "/caminho/arte.png"'},
                     ensure_ascii=False, indent=2))
    return job


def color_layer(mask: Image.Image, color: tuple) -> Image.Image:
    layer = Image.new("RGBA", SIZE, color)
    layer.putalpha(mask)
    return layer


def draw_headline(canvas: Image.Image, spec: dict) -> dict:
    layout = spec["layout_texto"]
    font = ImageFont.truetype(spec["fonte"], layout["tamanho_fonte"])
    mask = Image.new("L", SIZE)
    draw = ImageDraw.Draw(mask)
    y = HEADLINE_BOX[1] + (HEADLINE_BOX[3] - HEADLINE_BOX[1] - layout["altura"]) // 2
    boxes = []
    for line in layout["linhas"]:
        box = font.getbbox(line)
        width, height = box[2] - box[0], box[3] - box[1]
        x = (SIZE[0] - width) // 2
        draw.text((x - box[0], y - box[1]), line, font=font, fill=255)
        boxes.append([x, y, x + width, y + height])
        y += height + layout["intervalo"]
    outline = mask.filter(ImageFilter.MaxFilter(17))
    canvas.alpha_composite(color_layer(outline.filter(ImageFilter.GaussianBlur(10)), (0, 0, 0, 255)))
    pink = mask.filter(ImageFilter.MaxFilter(9))
    for radius in (19, 9):
        canvas.alpha_composite(color_layer(pink.filter(ImageFilter.GaussianBlur(radius)), (255, 0, 184, 255)))
    canvas.alpha_composite(color_layer(outline, (10, 0, 12, 255)))
    canvas.alpha_composite(color_layer(pink, (255, 12, 188, 255)))
    canvas.alpha_composite(color_layer(mask, (255, 255, 255, 255)))
    return {"linhas": layout["linhas"], "caixas": boxes, "tamanho_fonte": layout["tamanho_fonte"]}


def paste_photo(canvas: Image.Image, spec: dict) -> dict:
    photo = spec["foto"]
    with Image.open(photo["caminho"]) as im:
        cropped = im.convert("RGBA").crop(photo["recorte"])
    scale = min(PHOTO_LIMIT[0] / cropped.width, PHOTO_LIMIT[1] / cropped.height)
    dimensions = (round(cropped.width * scale), round(cropped.height * scale))
    resized = cropped.resize(dimensions, Image.Resampling.LANCZOS)
    x = (SIZE[0] - resized.width) // 2
    y = PHOTO_ORIGIN[1]
    face = photo.get("area_rosto")
    face_box = None
    fade_height = 24
    if face:
        left, top = photo["recorte"][:2]
        face_box = [x + round((face[0] - left) * scale), y + round((face[1] - top) * scale),
                    x + round((face[2] - left) * scale), y + round((face[3] - top) * scale)]
        if face_box[3] >= y + resized.height - fade_height:
            raise ValueError("A transicao da foto atingiria o rosto; ajuste o recorte antes de finalizar.")
    expected_face = None
    if face_box:
        selected = resized.crop((face_box[0]-x, face_box[1]-y, face_box[2]-x, face_box[3]-y))
        background = canvas.crop(face_box).convert("RGBA")
        expected_face = Image.alpha_composite(background, selected).convert("RGB")
    mask = Image.new("L", resized.size, 255)
    draw = ImageDraw.Draw(mask)
    for row in range(resized.height - fade_height, resized.height):
        value = round(255 * (resized.height - 1 - row) / (fade_height - 1))
        draw.line((0, row, resized.width, row), fill=value)
    resized.putalpha(ImageChops.multiply(resized.getchannel("A"), mask))
    canvas.alpha_composite(resized, (x, y))
    return {"origem": [x, y], "tamanho": list(dimensions), "escala_uniforme": scale,
            "area_rosto_final": face_box, "rosto_esperado": expected_face}


def draw_frame(canvas: Image.Image) -> None:
    frame = Image.new("RGBA", SIZE)
    d = ImageDraw.Draw(frame)
    d.rounded_rectangle((24, 24, 1056, 1896), radius=34, outline=(255, 0, 186, 255), width=13)
    canvas.alpha_composite(frame.filter(ImageFilter.GaussianBlur(17)))
    canvas.alpha_composite(frame.filter(ImageFilter.GaussianBlur(7)))
    canvas.alpha_composite(frame)
    ImageDraw.Draw(canvas).rounded_rectangle((27, 27, 1053, 1893), radius=31,
                                            outline=(255, 248, 255, 255), width=5)


def paste_badge(canvas: Image.Image, spec: dict) -> dict:
    badge = spec["badge"]
    with Image.open(badge["caminho"]) as im:
        resized = ImageOps.contain(im.convert("RGBA"), (badge["tamanho"], badge["tamanho"]), Image.Resampling.NEAREST)
    x, y = badge["posicao"]
    if x < 43 or y < 43 or x + resized.width > SIZE[0] - 43 or y + resized.height > 600:
        raise ValueError("A badge deve ficar dentro da borda e na parte superior da thumb.")
    glow = Image.new("RGBA", SIZE)
    silhouette = Image.new("RGBA", resized.size, (240, 0, 255, 255))
    silhouette.putalpha(resized.getchannel("A").point(lambda value: value // 2))
    glow.alpha_composite(silhouette, (x, y))
    canvas.alpha_composite(glow.filter(ImageFilter.GaussianBlur(8)))
    canvas.alpha_composite(resized, (x, y))
    return {"arquivo": badge["caminho"], "posicao": [x, y], "tamanho": list(resized.size),
            "png_original": True, "reesenhada": False}


def publish_png(canvas: Image.Image, root: Path) -> Path:
    """Valida o novo PNG antes de substituir o anterior e limpar output."""
    output = root / "output"
    if output.is_symlink():
        raise ValueError("output precisa ser uma pasta real, nao um link simbolico.")
    output.mkdir(exist_ok=True)
    final = output / "thumb.png"
    if final.is_dir() or final.is_symlink():
        raise ValueError("output/thumb.png precisa ser um arquivo regular.")
    descriptor, temporary_name = tempfile.mkstemp(prefix=".thumb-stage-", suffix=".png", dir=root)
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        canvas.convert("RGB").save(temporary, format="PNG", optimize=True)
        with Image.open(temporary) as check:
            if check.format != "PNG" or check.size != SIZE:
                raise ValueError("A imagem final nao e um PNG valido em 1080x1920.")
            check.verify()
        temporary.replace(final)
    finally:
        temporary.unlink(missing_ok=True)
    # output e reservado a uma unica entrega; entradas ficam fora dessa pasta.
    for old in output.iterdir():
        if old.name == "thumb.png":
            continue
        if old.is_dir() and not old.is_symlink():
            shutil.rmtree(old)
        else:
            old.unlink()
    return final


def cleanup_job(job: Path) -> None:
    """Remove apenas pedidos temporarios identificados como criados por este script."""
    job = job.resolve()
    if (job.parent == Path(tempfile.gettempdir()).resolve()
            and job.name.startswith("shorts-thumb-")
            and (job / ".shorts-thumb-job").is_file()):
        shutil.rmtree(job)


def finalize(args: argparse.Namespace) -> Path:
    job = Path(args.job).expanduser().resolve()
    spec = load_json(job / "pedido.json")
    if spec.get("versao") != 3 or tuple(spec["tamanho"]) != SIZE:
        raise ValueError("Pedido com versao ou dimensoes incompatíveis.")
    for key in ("foto", "badge"):
        path = Path(spec[key]["caminho"])
        if not path.is_file() or sha256(path) != spec[key]["sha256"]:
            raise ValueError(f"O arquivo de {key} mudou ou desapareceu. Execute preparar novamente.")
    validate_badge(Path(spec["badge"]["caminho"]))
    artwork = Path(args.arte).expanduser().resolve()
    with Image.open(artwork) as im:
        im.load()
        if min(im.size) < 400:
            raise ValueError("A arte do cartaz precisa ter pelo menos 400 px em cada lado.")
        art = im.convert("RGBA")
    canvas = Image.new("RGBA", SIZE, (5, 2, 11, 255))
    area = (POSTER_BOX[2] - POSTER_BOX[0], POSTER_BOX[3] - POSTER_BOX[1])
    backdrop = ImageOps.fit(art, area, Image.Resampling.LANCZOS).filter(ImageFilter.GaussianBlur(24))
    backdrop = Image.blend(backdrop, Image.new("RGBA", area, (5, 2, 11, 255)), .45)
    canvas.alpha_composite(backdrop, POSTER_BOX[:2])
    poster = ImageOps.contain(art, area, Image.Resampling.LANCZOS)
    poster_xy = (POSTER_BOX[0] + (area[0] - poster.width)//2, POSTER_BOX[1] + (area[1] - poster.height)//2)
    canvas.alpha_composite(poster, poster_xy)
    photo_report = paste_photo(canvas, spec)
    text_report = draw_headline(canvas, spec)
    draw_frame(canvas)
    badge_report = paste_badge(canvas, spec)
    expected = photo_report.pop("rosto_esperado")
    face_box = photo_report["area_rosto_final"]
    face_unchanged = None
    if expected is not None:
        face_unchanged = ImageChops.difference(canvas.convert("RGB").crop(face_box), expected).getbbox() is None
        if not face_unchanged:
            raise ValueError("A verificacao detectou sobreposicao sobre o rosto original. Ajuste o layout.")
    final = publish_png(canvas, Path(spec["raiz_projeto"]))
    report = {"thumb_png": str(final), "dimensoes": list(SIZE), "foto": photo_report,
              "rosto_igual_ao_asset_escolhido_redimensionado": face_unchanged,
              "expressao": spec["expressao"],
              "expressao_editada_por_IA": spec["foto"]["expressao_editada_por_IA"],
              "rosto_regenerado_na_montagem": False,
              "badge": badge_report, "texto": text_report, "cartaz_sem_esticar_ou_cortar": True,
              "bytes": final.stat().st_size, "sha256": sha256(final)}
    cleanup_job(job)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return final


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    sub = result.add_subparsers(dest="comando", required=True)
    prep = sub.add_parser("preparar", help="Recebe texto/contexto e grava os prompts para o agente.")
    prep.add_argument("--texto", required=True, help="Frase central, com acentos; quebras de linha opcionais.")
    prep.add_argument("--contexto", required=True, help="Tema do video e elementos desejados no cartaz.")
    prep.add_argument("--texto-cartaz", default="", help="Frase secundaria dentro do cartaz, se desejada.")
    prep.add_argument("--slug", help="Identificador do pedido temporario; a saida continua output/thumb.png.")
    prep.add_argument("--foto", help="Outra foto; usa a imagem inteira, sem recorte automatico do rosto.")
    prep.add_argument("--expressao", choices=("auto", *EXPRESSIONS), default="auto",
                      help="Asset de expressao. auto usa regras locais; o agente pode escolher explicitamente.")
    sub.add_parser("expressoes", help="Lista os assets de expressoes disponiveis.")
    finish = sub.add_parser("finalizar", help="Compoe foto, texto, PNG da badge, arte e moldura.")
    finish.add_argument("--job", required=True, help="Pasta do pedido criada por preparar.")
    finish.add_argument("--arte", required=True, help="Imagem de cartaz gerada pela ferramenta de imagens.")
    return result


def main() -> int:
    cli = parser()
    args = cli.parse_args()
    try:
        if args.comando == "preparar":
            prepare(args)
        elif args.comando == "expressoes":
            config = load_json(ROOT / "thumb.config.json")
            print(json.dumps(expression_catalog(ROOT, config), ensure_ascii=False, indent=2))
        else:
            finalize(args)
    except (ValueError, OSError, KeyError) as error:
        cli.exit(2, f"Erro: {error}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
