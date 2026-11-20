"""Catalogo de objetos por firma visual (bot/item_catalog.py) y conteo por tipo en RECOGER. Todo en tmp_path."""
import json
import logging
from pathlib import Path

import cv2
import numpy as np
import pytest

from bot.bot import State
from bot.config import DEFAULTS
from bot.geometry import Rect
from bot.item_catalog import (UNKNOWN_ID, ItemCatalog, SeenItem, label_signature, signature_similarity, validate_name,
                              vanished)
from bot.vision.items import find_items
from test_bot_states import H, W, Harness
from test_pickup_on_foot import DOWN, UP_RIGHT, Floor, label, start


def text_img(text: str, w=100, h=15, scale=0.4, pad=0):
    """Imagen con un recuadro oscuro y el texto en blanco puro (el criterio de `vision.items`)."""
    img = np.full((h, w, 3), 30, np.uint8)
    cv2.putText(img, text, (3 + pad, 11), cv2.FONT_HERSHEY_SIMPLEX, scale, (255, 255, 255), 1, cv2.LINE_8)
    return img


def sig(text: str, **kw):
    return label_signature(text_img(text, **kw), Rect(0, 0, 100, 15))


# ---------------------------------------------------------------- firma

def test_misma_etiqueta_misma_firma_aunque_cambie_de_lugar():
    a, b = sig("Perla"), sig("Perla", pad=7)
    assert a.shape[0] == 10 and a.shape == b.shape
    assert signature_similarity(a, b) > 0.99


def test_etiquetas_distintas_firmas_distintas():
    assert signature_similarity(sig("Perla"), sig("Rubi")) < 0.88
    assert signature_similarity(sig("Perla"), sig("Perla+")) < 0.88


def test_sin_texto_no_hay_firma():
    assert label_signature(np.full((15, 100, 3), 30, np.uint8), Rect(0, 0, 100, 15)) is None
    assert label_signature(np.zeros((5, 5, 3), np.uint8), Rect(50, 50, 10, 10)) is None


def test_ancho_muy_distinto_no_coincide():
    assert signature_similarity(np.ones((10, 20), np.float32), np.ones((10, 30), np.float32)) == 0.0


def test_firmas_reales_de_samples():
    p = Path(__file__).resolve().parent.parent / "samples" / "items_en_piso.png"
    if not p.is_file():
        pytest.skip("falta samples/items_en_piso.png")
    img = cv2.imread(str(p))
    sigs = [label_signature(img, i.rect) for i in find_items(img, DEFAULTS)]
    assert len(sigs) == 5 and all(s is not None and s.shape[0] == 10 for s in sigs)
    for i, a in enumerate(sigs):
        for j, b in enumerate(sigs):
            assert (signature_similarity(a, b) > 0.99) == (i == j)


# ---------------------------------------------------------------- catalogo

def test_agrupa_iguales_y_crea_nuevos(tmp_path):
    cat = ItemCatalog(tmp_path / "obj", match=0.88)
    crop = text_img("Perla")
    e1 = cat.match_or_add(sig("Perla"), crop)
    e2 = cat.match_or_add(sig("Perla", pad=3), crop)
    e3 = cat.match_or_add(sig("Rubi"), text_img("Rubi"))
    assert e1.id == e2.id != e3.id
    assert (e1.name, e1.count, e3.name, e3.count) == ("Objeto 1", 2, "Objeto 2", 1)
    assert (tmp_path / "obj" / f"{e1.id}.png").is_file() and (tmp_path / "obj" / f"{e1.id}.npy").is_file()
    assert cat.thumbnail_path(e1.id) is not None and cat.thumbnail_path("nada") is None


def test_persiste_y_reconoce_despues_de_recargar(tmp_path):
    cat = ItemCatalog(tmp_path)
    cat.match_or_add(sig("Perla"), text_img("Perla"))
    cat2 = ItemCatalog(tmp_path)
    assert [(e.name, e.count) for e in cat2.entries] == [("Objeto 1", 1)]
    assert cat2.match_or_add(sig("Perla"), None).id == cat.entries[0].id and cat2.entries[0].count == 2
    assert not list(tmp_path.glob("*.tmp"))


def test_renombrar(tmp_path):
    cat = ItemCatalog(tmp_path)
    e = cat.match_or_add(sig("Perla"), None)
    cat.rename(e.id, "  Perla azul ")
    assert ItemCatalog(tmp_path).name_of(e.id) == "Perla azul"
    for bad in ("", "   ", "x" * 200):
        with pytest.raises(ValueError):
            cat.rename(e.id, bad)
    with pytest.raises(ValueError):
        cat.rename("no_existe", "A")
    assert ItemCatalog(tmp_path).name_of(e.id) == "Perla azul"
    assert cat.name_of(UNKNOWN_ID) == "Objeto sin identificar" and cat.name_of("zzz") == "zzz"
    with pytest.raises(ValueError):
        validate_name(None)


def test_desactivado_no_escribe(tmp_path):
    cat = ItemCatalog(None)
    assert not cat.enabled
    e = cat.match_or_add(sig("Perla"), text_img("Perla"))
    assert e.name == "Objeto 1" and list(tmp_path.iterdir()) == []


def test_archivo_roto_va_a_bak_y_empieza_vacio(tmp_path, caplog):
    (tmp_path / "catalogo.json").write_text("{esto no es json", encoding="utf-8")
    with caplog.at_level(logging.WARNING):
        cat = ItemCatalog(tmp_path)
    assert len(cat) == 0 and "no se pudo leer" in caplog.text
    cat.match_or_add(sig("Perla"), None)
    assert (tmp_path / "catalogo.json.bak").read_text(encoding="utf-8") == "{esto no es json"
    assert json.loads((tmp_path / "catalogo.json").read_text(encoding="utf-8"))["items"][0]["name"] == "Objeto 1"


def test_falta_la_firma_no_rompe(tmp_path):
    cat = ItemCatalog(tmp_path)
    e = cat.match_or_add(sig("Perla"), None)
    (tmp_path / f"{e.id}.npy").unlink()
    cat2 = ItemCatalog(tmp_path)
    assert len(cat2) == 1 and cat2.match_or_add(sig("Perla"), None).id != e.id   # sin firma: no la reconoce


# ---------------------------------------------------------------- vanished

def seen(x, y, text):
    return SeenItem((x, y), sig(text), None)


def test_vanished_asocia_por_firma_aunque_se_mueva():
    prev = [seen(100, 100, "Perla"), seen(300, 100, "Rubi"), seen(500, 100, "Perla")]
    cur = [seen(120, 130, "Rubi"), seen(520, 130, "Perla")]       # todo corrido 20-30 px; falta una Perla
    gone = vanished(prev, cur, 0.88, (0, 0))
    assert len(gone) == 1 and gone[0].point == (100, 100)


def test_vanished_ordena_por_cercania_a_los_pies():
    prev = [seen(900, 900, "Perla"), seen(100, 100, "Rubi")]
    assert [g.point for g in vanished(prev, [], 0.88, (0, 0))] == [(100, 100), (900, 900)]


def test_vanished_sin_firma_se_asocia_entre_si():
    a, b = SeenItem((1, 1), None, None), SeenItem((2, 2), None, None)
    assert vanished([a, b], [SeenItem((1, 1), None, None)], 0.88, (0, 0)) == [b]
    assert vanished([a], [seen(1, 1, "Perla")], 0.88, (0, 0)) == [a]


# ---------------------------------------------------------------- conteo en RECOGER

def render(items):
    """Imagen del juego con cada etiqueta (centro it[0], it[1]) dibujada con su texto (it[2])."""
    img = np.zeros((H, W, 3), np.uint8)
    for it in items:
        r = label(it).rect
        img[r.y:r.bottom, r.x:r.right] = 30
        cv2.putText(img, it[2], (r.x + 4, r.y + 11), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (255, 255, 255), 1, cv2.LINE_8)
    return img


def named_harness(pts_names, catalog, blind=False):
    box = {}
    h = Harness(lambda h, n: box["f"](h, n), bot_kwargs={"catalog": catalog})
    f = Floor(h, [p for p, _ in pts_names])
    for it, (_, nm) in zip(f.items, pts_names):
        it.append(nm)

    def call(h, n):
        p = f(h, n)
        h.bot._img = None if blind else render(f.items)
        return p
    box["f"], h.floor = call, f
    return h


def collect(h):
    start(h)
    h.run_until(lambda: h.bot.state is not State.RECOGER)


def test_cuenta_objetos_por_tipo_y_loguea(tmp_path, caplog):
    cat = ItemCatalog(tmp_path)
    h = named_harness([(DOWN, "Perla"), (UP_RIGHT, "Rubi")], cat)
    with caplog.at_level(logging.INFO):
        collect(h)
    st = h.bot.stats
    assert st.items == 2 and sum(st.items_by_type.values()) == 2 and UNKNOWN_ID not in st.items_by_type
    assert sorted(st.items_by_type.values()) == [1, 1] and len(cat) == 2
    assert "Objeto recogido: Objeto" in caplog.text and "(total en la sesion: 1)" in caplog.text


def test_mismo_tipo_se_agrupa(tmp_path):
    cat = ItemCatalog(tmp_path)
    h = named_harness([(DOWN, "Perla"), (UP_RIGHT, "Perla")], cat)
    collect(h)
    assert h.bot.stats.items == 2 and list(h.bot.stats.items_by_type.values()) == [2] and len(cat) == 1


def test_sin_imagen_cuenta_como_sin_identificar(tmp_path):
    cat = ItemCatalog(tmp_path)
    h = named_harness([(DOWN, "Perla")], cat, blind=True)
    collect(h)
    assert h.bot.stats.items == 1 and h.bot.stats.items_by_type == {UNKNOWN_ID: 1} and len(cat) == 0


def test_catalogo_desactivado_solo_cuenta_items():
    h = named_harness([(DOWN, "Perla")], ItemCatalog(None))
    collect(h)
    assert h.bot.stats.items == 1 and h.bot.stats.items_by_type == {}
