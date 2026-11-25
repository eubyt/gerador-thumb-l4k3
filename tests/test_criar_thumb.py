import argparse
from contextlib import redirect_stdout
import json
import io
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest import mock

from PIL import Image, ImageChops
import criar_thumb as app


class ThumbnailFlowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for relative in ("ref/desempregado.png", "assets/badge-streamer.png", "expressoes.json",
                         "assets/desempregado_feliz.png", "assets/desempregado_surpreso.png",
                         "assets/desempregado_triste.png", "assets/desempregado_emocionado.png", "ref/thumbnails/thumbnail_layout_reference.png"):
            target = self.root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(app.ROOT / relative, target)
        shutil.copy2(app.ROOT / "thumb.config.json", self.root / "thumb.config.json")
        self.art = self.root / "cartaz.png"
        Image.new("RGB", (1152, 1024), (39, 71, 67)).save(self.art)

    def prepare(self, **changes):
        values = dict(texto="ELES ESTÃO LÁ FORA", contexto="GTA VI: aliens em Leonida",
                      texto_cartaz="ALIENS EM LEONIDA?", slug="teste", foto=None, expressao="neutro")
        values.update(changes)
        with redirect_stdout(io.StringIO()):
            job = app.prepare(argparse.Namespace(**values), self.root)
        self.addCleanup(app.cleanup_job, job)
        return job

    def finish(self, job, **changes):
        values = dict(job=str(job), arte=str(self.art))
        values.update(changes)
        output = io.StringIO()
        with redirect_stdout(output):
            result = app.finalize(argparse.Namespace(**values))
        self.report = json.loads(output.getvalue())
        return result

    def assert_single_png(self):
        self.assertEqual(sorted(p.name for p in (self.root / "output").iterdir()), ["thumb.png"])

    def test_end_to_end_preserves_face_and_badge_with_only_final_png(self):
        job = self.prepare()
        self.assertFalse((self.root / "output").exists())
        self.assertEqual(job.parent, Path(tempfile.gettempdir()).resolve())
        spec = app.load_json(job / "pedido.json")
        self.assertEqual(Path(spec["foto"]["caminho"]), (self.root / "ref/desempregado.png").resolve())
        self.assertNotIn(str((self.root / "ref/desempregado.png").resolve()), spec["referencias"])
        self.assertIn(str((self.root / "ref/thumbnails/thumbnail_layout_reference.png").resolve()), spec["referencias"])
        self.assertIn("badge-streamer.png", (job / "prompt.txt").read_text())
        self.assertIn("f9954507-dd06-4d57-9d2d-7be5b4f57361/3", (job / "prompt-arte.txt").read_text())
        final = self.finish(job)
        report = self.report
        self.assert_single_png()
        self.assertFalse(job.exists())
        self.assertTrue(report["rosto_igual_ao_asset_escolhido_redimensionado"])
        with Image.open(final) as im:
            self.assertEqual(im.size, (1080, 1920))
            self.assertEqual(im.format, "PNG")
            with Image.open(spec["foto"]["caminho"]) as photo:
                crop = photo.convert("RGBA").crop(spec["foto"]["recorte"])
            expected = crop.resize(tuple(report["foto"]["tamanho"]), Image.Resampling.LANCZOS)
            x, y = report["foto"]["origem"]
            box = report["foto"]["area_rosto_final"]
            expected_face = expected.crop((box[0]-x, box[1]-y, box[2]-x, box[3]-y)).convert("RGB")
            self.assertIsNone(ImageChops.difference(im.crop(box), expected_face).getbbox())
            with Image.open(spec["badge"]["caminho"]) as badge:
                resized = badge.convert("RGBA").resize((112, 112), Image.Resampling.NEAREST)
            bx, by = report["badge"]["posicao"]
            actual = im.crop((bx, by, bx+112, by+112))
            opaque = resized.getchannel("A").point(lambda v: 255 if v == 255 else 0)
            diff = ImageChops.difference(actual, resized.convert("RGB"))
            self.assertIsNone(ImageChops.multiply(diff, Image.merge("RGB", (opaque, opaque, opaque))).getbbox())
        self.assertEqual(report["texto"]["linhas"], ["ELES ESTÃO", "LÁ FORA"])

    def test_next_thumb_replaces_previous_without_versions(self):
        final = self.finish(self.prepare())
        before = app.sha256(final)
        job = self.prepare(texto="ALIENS NO GTA VI")
        self.assertEqual(app.sha256(final), before)
        self.assertEqual(self.finish(job), final)
        self.assertNotEqual(app.sha256(final), before)
        self.assert_single_png()

    def test_output_cleanup_does_not_follow_symlinks(self):
        output = self.root / "output"
        (output / "antigo").mkdir(parents=True)
        (output / "antigo/prompt.txt").write_text("velho")
        (output / "thumb.jpg").write_text("velho")
        external = self.root / "preservar.txt"
        external.write_text("entrada")
        (output / "atalho").symlink_to(external)
        self.finish(self.prepare())
        self.assert_single_png()
        self.assertEqual(external.read_text(), "entrada")

    def test_encoding_failure_preserves_previous_and_cleans_stage(self):
        final = self.finish(self.prepare())
        before = app.sha256(final)
        job = self.prepare()
        with mock.patch.object(Image.Image, "save", side_effect=OSError("falha simulada")):
            with self.assertRaisesRegex(OSError, "falha simulada"):
                self.finish(job)
        self.assertEqual(app.sha256(final), before)
        self.assert_single_png()
        self.assertFalse(list(self.root.glob(".thumb-stage-*")))

    def test_modified_photo_is_rejected_and_previous_remains(self):
        final = self.finish(self.prepare())
        before = app.sha256(final)
        job = self.prepare()
        Image.new("RGB", (518, 318), "red").save(self.root / "ref/desempregado.png")
        with self.assertRaisesRegex(ValueError, "foto mudou"):
            self.finish(job)
        self.assertEqual(app.sha256(final), before)
        self.assert_single_png()

    def test_badge_needs_real_png_and_transparency(self):
        badge = self.root / "assets/badge-streamer.png"
        Image.new("RGB", (72, 72), "red").save(badge, format="JPEG")
        with self.assertRaisesRegex(ValueError, "PNG verdadeiro"):
            self.prepare()
        Image.new("RGB", (72, 72), "red").save(badge, format="PNG")
        with self.assertRaisesRegex(ValueError, "canal alpha"):
            self.prepare()

    def test_bad_input_and_missing_art_fail_cleanly(self):
        for changes in (dict(texto=" "), dict(contexto=" "), dict(slug="../escape"),
                        dict(texto="PALAVRA " * 25)):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.prepare(**changes)
        job = self.prepare()
        with self.assertRaises(FileNotFoundError):
            self.finish(job, arte=str(self.root / "ausente.png"))
        self.assertFalse((self.root / "output").exists())

    def test_new_photo_uses_entire_image(self):
        custom = self.root / "outra-foto.png"
        Image.new("RGB", (600, 900), "navy").save(custom)
        job = self.prepare(foto=str(custom))
        spec = app.load_json(job / "pedido.json")
        self.assertEqual(spec["foto"]["recorte"], [0, 0, 600, 900])
        self.finish(job)
        self.assertIsNone(self.report["rosto_igual_ao_asset_escolhido_redimensionado"])
        self.assert_single_png()

    def test_auto_selection_and_explicit_choice(self):
        cases = [
            ("Vitoria e conquista", "feliz"),
            ("Revelacao de aliens e OVNI", "surpreso"),
            ("Despedida triste", "triste"),
            ("Homenagem e reencontro", "emocionado"),
            ("Analise tecnica de desempenho", "neutro"),
        ]
        for context, name in cases:
            with self.subTest(context=context):
                job = self.prepare(expressao="auto", contexto=context)
                spec = app.load_json(job / "pedido.json")
                self.assertEqual(spec["expressao"]["nome"], name)
                self.assertEqual(spec["expressao"]["selecao"], "regra_local")
                self.assertIn(name, (job / "prompt-arte.txt").read_text())
        job = self.prepare(expressao="triste", contexto="Aliens e OVNI")
        spec = app.load_json(job / "pedido.json")
        self.assertEqual(spec["expressao"]["nome"], "triste")
        self.assertEqual(spec["expressao"]["selecao"], "explicita")

    def test_every_generated_expression_renders_without_modifying_selected_face(self):
        for name in ("feliz", "surpreso", "triste", "emocionado"):
            with self.subTest(expressao=name):
                job = self.prepare(expressao=name)
                spec = app.load_json(job / "pedido.json")
                final = self.finish(job)
                self.assert_single_png()
                self.assertTrue(self.report["expressao_editada_por_IA"])
                self.assertFalse(self.report["rosto_regenerado_na_montagem"])
                self.assertTrue(self.report["rosto_igual_ao_asset_escolhido_redimensionado"])
                with Image.open(spec["foto"]["caminho"]) as source:
                    expected = source.convert("RGBA").resize(tuple(self.report["foto"]["tamanho"]), Image.Resampling.LANCZOS)
                x, y = self.report["foto"]["origem"]
                box = self.report["foto"]["area_rosto_final"]
                face = expected.crop((box[0]-x, box[1]-y, box[2]-x, box[3]-y))
                expected_face = Image.alpha_composite(Image.new("RGBA", face.size, (5, 2, 11, 255)), face).convert("RGB")
                with Image.open(final) as im:
                    self.assertIsNone(ImageChops.difference(im.crop(box), expected_face).getbbox())

    def test_invalid_or_missing_expression_has_no_silent_substitution(self):
        with self.assertRaisesRegex(ValueError, "desconhecida"):
            self.prepare(expressao="inexistente")
        (self.root / "assets/desempregado_feliz.png").unlink()
        with self.assertRaisesRegex(ValueError, "nao foi encontrado"):
            self.prepare(expressao="feliz")
        with self.assertRaisesRegex(ValueError, "nao misture"):
            self.prepare(expressao="feliz", foto="ref/desempregado.png")


if __name__ == "__main__":
    unittest.main()
