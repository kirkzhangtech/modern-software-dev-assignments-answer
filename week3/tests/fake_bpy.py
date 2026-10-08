"""A minimal stand-in for Blender's ``bpy`` module.

The add-on can only be exercised inside Blender, which makes it the hardest part
of the project to verify. This fake implements just enough of the surface the
add-on touches, so its command handlers can be tested headlessly: object
creation, transforms, materials, and rendering.

It is deliberately small. Anything the add-on does not call is left out, and
nothing here tries to be a real 3D kernel.
"""

from __future__ import annotations

import json
import os
import types
from typing import Any


class FakeVector(list):
    """Behaves like Blender's ``Vector``: indexable, iterable, and assignable."""

    def __init__(self, values=()):
        super().__init__(float(value) for value in values)
        while len(self) < 3:
            self.append(0.0)

    def __setitem__(self, index, value):
        if isinstance(index, slice):
            super().__setitem__(index, [float(v) for v in value])
        else:
            super().__setitem__(index, float(value))

    @property
    def x(self) -> float:
        return self[0]

    @property
    def y(self) -> float:
        return self[1]

    @property
    def z(self) -> float:
        return self[2]


class FakeSocket:
    """A shader node input socket carrying a default value."""

    def __init__(self, name: str, default: Any) -> None:
        self.name = name
        self.default_value = default


class FakeNode:
    def __init__(self, node_type: str, inputs: dict[str, FakeSocket]) -> None:
        self.type = node_type
        self.inputs = inputs


class FakeNodeTree:
    def __init__(self) -> None:
        self.nodes = [
            FakeNode(
                "BSDF_PRINCIPLED",
                {
                    "Base Color": FakeSocket("Base Color", (0.8, 0.8, 0.8, 1.0)),
                    "Metallic": FakeSocket("Metallic", 0.0),
                    "Roughness": FakeSocket("Roughness", 0.5),
                },
            )
        ]


class FakeMaterial:
    def __init__(self, name: str) -> None:
        self.name = name
        self.use_nodes = False
        self._node_tree = FakeNodeTree()

    @property
    def node_tree(self):
        return self._node_tree if self.use_nodes else None


class FakeMesh:
    def __init__(self) -> None:
        self.vertices: list[Any] = []
        self.polygons: list[Any] = []
        self.materials = _MaterialList()


class _MaterialList(list):
    """Mirrors real Blender, where editing ``mesh.materials`` updates the slots.

    The add-on assigns materials through ``obj.data.materials``, so the fake has
    to keep ``obj.material_slots`` consistent the way Blender does.
    """

    owner: FakeObject | None = None

    def append(self, material) -> None:
        super().append(material)
        if self.owner is not None:
            self.owner.material_slots = [FakeMaterialSlot(m) for m in self]

    def __setitem__(self, index, material):
        super().__setitem__(index, material)
        if self.owner is not None:
            self.owner.material_slots = [FakeMaterialSlot(m) for m in self]


class FakeLightData:
    def __init__(self, light_type: str) -> None:
        self.type = light_type


class FakeMaterialSlot:
    def __init__(self, material: FakeMaterial) -> None:
        self.material = material


class FakeObject:
    def __init__(self, name: str, object_type: str) -> None:
        self.name = name
        self.type = object_type
        self.location = FakeVector()
        self.rotation_euler = FakeVector()
        self.scale = FakeVector((1.0, 1.0, 1.0))
        self.dimensions = FakeVector((2.0, 2.0, 2.0))
        self.hide_viewport = False
        self.parent = None
        self.modifiers: list[Any] = []
        self.material_slots: list[FakeMaterialSlot] = []
        self.data: Any = FakeMesh() if object_type == "MESH" else None
        if isinstance(self.data, FakeMesh):
            self.data.materials.owner = self


class FakeObjectCollection:
    """Mimics ``bpy.data.objects`` for the operations the add-on performs."""

    def __init__(self) -> None:
        self._objects: dict[str, FakeObject] = {}

    def get(self, name: str):
        return self._objects.get(name)

    def add(self, obj: FakeObject) -> FakeObject:
        self._objects[obj.name] = obj
        return obj

    def remove(self, obj: FakeObject, do_unlink: bool = False) -> None:
        self._objects.pop(obj.name, None)

    def __iter__(self):
        return iter(self._objects.values())

    def __len__(self) -> int:
        return len(self._objects)

    def clear(self) -> None:
        self._objects.clear()


class FakeMaterialCollection:
    def __init__(self) -> None:
        self._materials: dict[str, FakeMaterial] = {}

    def get(self, name: str):
        return self._materials.get(name)

    def new(self, name: str) -> FakeMaterial:
        material = FakeMaterial(name)
        self._materials[name] = material
        return material

    def __iter__(self):
        return iter(self._materials.values())


class FakeRender:
    def __init__(self) -> None:
        self.filepath = ""
        self.resolution_x = 1920
        self.resolution_y = 1080
        self.engine = "BLENDER_EEVEE_NEXT"


class FakeScene:
    def __init__(self) -> None:
        self.name = "Scene"
        self.frame_current = 1
        self.objects: list[FakeObject] = []
        self.camera: FakeObject | None = None
        self.render = FakeRender()


class FakeEnumItem:
    def __init__(self, identifier: str) -> None:
        self.identifier = identifier


class FakeRNAProperty:
    def __init__(self, identifiers: list[str]) -> None:
        self.enum_items = [FakeEnumItem(identifier) for identifier in identifiers]


class FakeRenderSettingsRNA:
    properties = {"engine": FakeRNAProperty(["BLENDER_EEVEE_NEXT", "CYCLES", "BLENDER_WORKBENCH"])}


class FakeBlender:
    """The fake ``bpy`` module. Access :attr:`module` to install it in sys.modules."""

    def __init__(self, fail_opengl: bool = False, write_files: bool = True) -> None:
        self.fail_opengl = fail_opengl
        self.write_files = write_files
        self.registered_classes: list[Any] = []
        self.counter = 0

        # --- data ---------------------------------------------------------
        self.data = types.SimpleNamespace(
            objects=FakeObjectCollection(),
            materials=FakeMaterialCollection(),
            filepath="",
        )

        # --- context ------------------------------------------------------
        self.scene = FakeScene()
        self.context = types.SimpleNamespace(
            scene=self.scene, active_object=None, preferences=None
        )

        # --- app ----------------------------------------------------------
        self.timers_registered: list[Any] = []
        self.app = types.SimpleNamespace(
            version_string="4.2.0",
            timers=types.SimpleNamespace(register=self._register_timer),
        )

        # --- operators ----------------------------------------------------
        self.ops = types.SimpleNamespace(
            mesh=types.SimpleNamespace(
                primitive_cube_add=self._primitive("MESH", "Cube"),
                primitive_uv_sphere_add=self._primitive("MESH", "Sphere"),
                primitive_cylinder_add=self._primitive("MESH", "Cylinder"),
                primitive_plane_add=self._primitive("MESH", "Plane"),
                primitive_cone_add=self._primitive("MESH", "Cone"),
                primitive_torus_add=self._primitive("MESH", "Torus"),
            ),
            object=types.SimpleNamespace(
                camera_add=self._primitive("CAMERA", "Camera"),
                light_add=self._light_add,
            ),
            render=types.SimpleNamespace(render=self._render, opengl=self._opengl),
        )

        # --- types / utils / props ---------------------------------------
        self.types = types.SimpleNamespace(
            Operator=type("Operator", (), {}),
            Panel=type("Panel", (), {}),
            AddonPreferences=type("AddonPreferences", (), {}),
            RenderEngine=type("RenderEngine", (), {}),
            RenderSettings=types.SimpleNamespace(bl_rna=FakeRenderSettingsRNA()),
        )
        self.utils = types.SimpleNamespace(
            register_class=self.registered_classes.append, unregister_class=lambda cls: None
        )
        self.props = types.SimpleNamespace(
            StringProperty=lambda **kwargs: kwargs.get("default", ""),
            IntProperty=lambda **kwargs: kwargs.get("default", 0),
        )

        self.module = types.SimpleNamespace(
            app=self.app,
            context=self.context,
            data=self.data,
            ops=self.ops,
            types=self.types,
            utils=self.utils,
            props=self.props,
        )

    # -- helpers used by tests -------------------------------------------- #

    def add_object(
        self, name: str, object_type: str = "MESH", location=(0.0, 0.0, 0.0)
    ) -> FakeObject:
        obj = FakeObject(name, object_type)
        obj.location = FakeVector(location)
        self.data.objects.add(obj)
        self.scene.objects.append(obj)
        self.context.active_object = obj
        return obj

    def make_mesh(self, name: str, location=(0.0, 0.0, 0.0), vertices: int = 8) -> FakeObject:
        obj = self.add_object(name, "MESH", location)
        obj.data.vertices = [object() for _ in range(vertices)]
        obj.data.polygons = [object() for _ in range(6)]
        return obj

    # -- operator doubles -------------------------------------------------- #

    def _register_timer(self, func, first_interval=0.0):
        self.timers_registered.append(func)

    def _next_name(self, prefix: str) -> str:
        self.counter += 1
        return f"{prefix}.{self.counter:03d}"

    def _primitive(self, object_type: str, prefix: str):
        def operator(size=2.0, location=(0.0, 0.0, 0.0), **kwargs):
            obj = self.add_object(self._next_name(prefix), object_type, location)
            obj.dimensions = FakeVector((size, size, size))
            return {"FINISHED"}

        return operator

    def _light_add(self, type="POINT", location=(0.0, 0.0, 0.0), **kwargs):
        obj = self.add_object(self._next_name("Light"), "LIGHT", location)
        obj.data = FakeLightData(type)
        return {"FINISHED"}

    def _render(self, write_still=False, **kwargs):
        if write_still and self.write_files and self.scene.render.filepath:
            directory = os.path.dirname(self.scene.render.filepath)
            if directory:
                os.makedirs(directory, exist_ok=True)
            with open(self.scene.render.filepath, "wb") as handle:
                handle.write(b"\x89PNG\r\n\x1a\nfake-render")
        return {"FINISHED"}

    def _opengl(self, write_still=False, **kwargs):
        if self.fail_opengl:
            raise RuntimeError("Cannot use OpenGL render in background mode")
        if write_still and self.write_files and self.scene.render.filepath:
            directory = os.path.dirname(self.scene.render.filepath)
            if directory:
                os.makedirs(directory, exist_ok=True)
            with open(self.scene.render.filepath, "wb") as handle:
                handle.write(b"\x89PNG\r\n\x1a\nfake-viewport")
        return {"FINISHED"}


def dump(value: Any) -> str:
    """Debug helper: render a value as JSON for assertion messages."""
    return json.dumps(value, default=str, indent=2)
