import pytest

NAMES = ["captcha", "explorando_zona", "items_en_piso", "metin_avaricia_seleccionado",
         "metin_cerca_sin_seleccionar", "metin_muy_lejos", "metin_oscuridad_a_distancia",
         "metin_oscuridad_combate_vida_parcial", "metin_oscuridad_seleccionado_vida_llena",
         "muerte", "yang_en_piso"]


@pytest.mark.parametrize("name", NAMES)
def test_sample(load_sample, name):
    """Las samples son capturas de la ventana real, de tamaño variable (1846-1858 x 1010-1064),
    escala 1:1: las posiciones absolutas medidas en ellas no son confiables."""
    img = load_sample(f"{name}.png")
    assert img.ndim == 3 and img.shape[2] == 3, f"canales: {img.shape}"
    h, w = img.shape[:2]
    assert 1800 <= w <= 1920 and 1000 <= h <= 1080, f"tamaño real (alto, ancho): {(h, w)}"
