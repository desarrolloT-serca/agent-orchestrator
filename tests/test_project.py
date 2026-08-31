"""Check minimo de la deteccion de stack. Ejecutable con pytest o directamente."""

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from orchestrator import project


def _repo(files: dict[str, str]) -> Path:
    root = Path(tempfile.mkdtemp())
    for name, content in files.items():
        (root / name).write_text(content, encoding="utf-8")
    (root / "src").mkdir()
    return root


def test_nextjs_pnpm():
    pkg = json.dumps({
        "name": "nexora",
        "scripts": {"test": "vitest", "build": "next build"},
        "dependencies": {"next": "15.0.0"},
    })
    cfg = project.detect(_repo({"package.json": pkg, "pnpm-lock.yaml": ""}))
    assert cfg["project"] == {"name": "nexora", "profile": "nextjs"}
    assert cfg["commands"] == {"install": "pnpm install", "test": "pnpm test", "build": "pnpm build"}
    assert cfg["paths"]["source"] == ["src"]


def test_java_maven():
    cfg = project.detect(_repo({"pom.xml": "<project/>"}))
    assert cfg["project"]["profile"] == "java-maven"
    assert cfg["commands"]["test"] == "mvn -B test"


def test_java_maven_prefiere_el_wrapper():
    root = _repo({"pom.xml": "<project/>", "mvnw": ""})
    cmd = project.detect(root)["commands"]["test"]
    assert "mvn " not in cmd and "mvnw" in cmd


def test_save_load_roundtrip():
    root = _repo({"pom.xml": "<project/>"})
    project.save(root, project.detect(root))
    assert project.load(root)["project"]["profile"] == "java-maven"


def test_config_por_capas():
    root = _repo({"pom.xml": "<project/>"})
    project.save(root, project.detect(root))
    project.GLOBAL_CONFIG = root / "global.yaml"  # no existe todavia

    cfg = project.resolved(root)
    assert cfg["workers"]["default_model"] == "deepseek-v4-flash"
    assert cfg["limits"]["max_cost_usd"] == 5
    assert cfg["project"]["profile"] == "java-maven"

    # el config global sobrescribe los defaults, sin borrar lo demas
    project.GLOBAL_CONFIG.write_text(
        "workers:\n  default_model: deepseek-v4-pro\nlimits:\n  max_cost_usd: 1\n", encoding="utf-8")
    cfg = project.resolved(root)
    assert cfg["workers"]["default_model"] == "deepseek-v4-pro"
    assert cfg["workers"]["max_parallel"] == 3
    assert cfg["limits"]["max_cost_usd"] == 1 and cfg["limits"]["max_iterations"] == 60

    # el proyecto manda sobre el global
    propia = project.load(root)
    propia["workers"] = {"default_model": "deepseek-v4-flash"}
    project.save(root, propia)
    assert project.resolved(root)["workers"]["default_model"] == "deepseek-v4-flash"

    # y el project.yaml solo guarda diferencias: init no escribe workers ni limits
    assert "limits" not in project.detect(root)


if __name__ == "__main__":
    test_nextjs_pnpm()
    test_java_maven()
    test_save_load_roundtrip()
    print("ok")
