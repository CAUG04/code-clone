"""Pruebas de AppSpec: la normalización de nombres es donde se esconden los bugs."""
import keyword

import pytest

from agent.spec import FIELD_TYPES, AppSpec, Entity, Field, to_kebab, to_pascal, to_snake


class TestNombres:
    @pytest.mark.parametrize(
        "entrada,kebab",
        [
            ("NotaFácil", "nota-facil"),          # tildes: antes daba "nota-f-cil"
            ("Niños Felices", "ninos-felices"),   # ñ
            ("Gestión de Inventario", "gestion-de-inventario"),
            ("Café & Té", "cafe-te"),
            ("MiApp", "mi-app"),
            ("mi_app_genial", "mi-app-genial"),
            ("  espacios  raros  ", "espacios-raros"),
        ],
    )
    def test_acentos_y_simbolos(self, entrada, kebab):
        assert to_kebab(entrada) == kebab

    def test_nombres_vacios_no_rompen(self):
        assert to_kebab("") == "my-app"
        assert to_snake("") == "item"
        assert to_pascal("") == "Item"
        assert to_kebab("!!!") == "my-app"

    def test_nombres_que_empiezan_con_numero(self):
        """Un identificador no puede empezar con dígito."""
        assert not to_snake("7 eleven")[0].isdigit()
        assert not to_pascal("7 eleven")[0].isdigit()

    @pytest.mark.parametrize("plural_de,esperado", [
        ("Category", "categories"), ("Box", "boxes"), ("Dish", "dishes"),
        ("Product", "products"), ("Day", "days"), ("Class", "classes"),
    ])
    def test_plurales(self, plural_de, esperado):
        assert Entity(name=plural_de).plural == esperado


class TestCampos:
    @pytest.mark.parametrize("nombre", ["class", "def", "return", "import", "None"])
    def test_palabras_reservadas_de_python_se_renombran(self, nombre):
        f = Field(nombre)
        assert not keyword.iskeyword(f.name), f"'{f.name}' es palabra reservada"

    @pytest.mark.parametrize("nombre", ["owner_id", "created_at", "model_config", "metadata"])
    def test_nombres_internos_se_renombran(self, nombre):
        """Chocarían con las columnas que agrega el generador."""
        assert Field(nombre).name != nombre

    def test_tipo_desconocido_cae_en_string(self):
        assert Field("x", "tipo-inventado").type == "string"

    def test_todos_los_tipos_tienen_las_cinco_traducciones(self):
        for tipo, valores in FIELD_TYPES.items():
            assert len(valores) == 5, f"{tipo} está incompleto"
            f = Field("x", tipo)
            assert f.sa_type and f.py_type and f.ts_type and f.input_type
            assert f.test_literal, f"{tipo} no tiene valor de prueba"

    def test_los_valores_de_prueba_son_literales_python_validos(self):
        for tipo in FIELD_TYPES:
            valor = eval(Field("x", tipo).test_literal)  # noqa: S307 - literal propio
            assert valor is not None


class TestEntidades:
    def test_se_quita_el_campo_id(self):
        """El generador crea el id; si viene en el spec, se descarta."""
        e = Entity(name="X", fields=[{"name": "id", "type": "integer"}, {"name": "a"}])
        assert [f.name for f in e.fields] == ["a"]

    def test_se_quitan_campos_duplicados(self):
        e = Entity(name="X", fields=[{"name": "nombre"}, {"name": "Nombre"}, {"name": "b"}])
        assert [f.name for f in e.fields] == ["nombre", "b"]

    def test_nombres_de_entidad_reservados_se_renombran(self):
        """'User' chocaría con el modelo de autenticación."""
        assert Entity(name="User").name == "AppUser"
        assert Entity(name="Token").name == "AppToken"

    def test_display_field_prefiere_name_o_title(self):
        assert Entity(name="X", fields=[{"name": "codigo"}, {"name": "name"}]).display_field == "name"
        assert Entity(name="X", fields=[{"name": "codigo"}]).display_field == "codigo"
        # Sin campos de texto, cae en id
        assert Entity(name="X", fields=[{"name": "cantidad", "type": "integer"}]).display_field == "id"


class TestAppSpec:
    def test_color_invalido_cae_en_el_default(self):
        for malo in ["rojo", "#XYZ", "", "#1234567"]:
            assert AppSpec("A", "t", "d", "web", color_primary=malo).color_primary == "#6366f1"
        assert AppSpec("A", "t", "d", "web", color_primary="#Ff00aa").color_primary == "#Ff00aa"

    def test_entidades_duplicadas_se_colapsan(self):
        s = AppSpec("A", "t", "d", "web", entities=[
            {"name": "Product", "fields": []},
            {"name": "product", "fields": []},
        ])
        assert len(s.entities) == 1

    @pytest.mark.parametrize("plataforma,web,movil", [
        ("web", True, False), ("mobile", False, True), ("both", True, True),
    ])
    def test_banderas_de_plataforma(self, plataforma, web, movil):
        s = AppSpec("A", "t", "d", plataforma)
        assert s.wants_web is web and s.wants_mobile is movil

    def test_ida_y_vuelta_por_json(self, tmp_path, spec_both):
        archivo = tmp_path / "spec.json"
        archivo.write_text(spec_both.to_json(), encoding="utf-8")
        assert AppSpec.from_json_file(archivo).to_json() == spec_both.to_json()

    def test_from_dict_ignora_campos_desconocidos(self):
        s = AppSpec.from_dict({
            "app_name": "A", "tagline": "t", "description": "d", "platform": "web",
            "campo_que_no_existe": "x", "otro": 123,
        })
        assert s.app_name == "A"
