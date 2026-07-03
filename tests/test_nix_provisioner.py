# --- RFC-0005 §3.2 Tier C (#66): content-addressed digest + tier resolver ---


def test_resolve_tier_maps_methods_and_defaults_to_nix():
    from tools.runners.nix_provisioner import resolve_tier

    assert resolve_tier({"provisioning": {"method": "nix"}}) == "nix"
    assert resolve_tier({"provisioning": {"method": "image"}}) == "catalog"
    assert resolve_tier({"provisioning": {"method": "build"}}) == "build"
    assert resolve_tier({"provisioning": {"method": "setup"}}) == "setup"
    assert resolve_tier({}) == "nix"  # default
    assert resolve_tier({"provisioning": {"method": "weird"}}) == "nix"  # degrade


def test_manifest_digest_is_content_addressed_by_environment_not_commands():
    from tools.runners.nix_provisioner import manifest_digest

    base = {
        "language": "python",
        "toolchain": {"python": "3.12"},
        "system_packages": ["go", "cmake"],
        "network": "restricted",
        "provisioning": {"method": "nix"},
    }
    same_env_diff_cmds = dict(
        base, verify_commands=["pytest -q tests/api"], system_packages=["cmake", "go"]
    )  # reordered
    diff_toolchain = {
        "language": "rust",
        "toolchain": {"rust": "1.90"},
        "provisioning": {"method": "build"},
    }
    assert manifest_digest(base) == manifest_digest(same_env_diff_cmds)  # cache hit
    assert manifest_digest(base) != manifest_digest(diff_toolchain)
    d = manifest_digest(base)
    assert len(d) == 16 and d == manifest_digest(base)  # stable


# --- Go toolchain (Go test-execution lane) ---


def test_generate_flake_go_toolchain_and_tools():
    from tools.runners.nix_provisioner import generate_flake

    env = {
        "language": "go",
        "toolchain": {"go": "1.22"},
        "system_packages": ["gotestsum", "gocover-cobertura"],
        "verify_commands": ["go test ./..."],
        "provisioning": {"method": "nix", "ref": "flake.nix", "generated": True},
    }
    flake = generate_flake(env)
    # pinned minor -> explicit attr; test+coverage tools ride in as system pkgs.
    assert "pkgs.go_1_22" in flake, flake
    assert "pkgs.gotestsum" in flake and "pkgs.gocover-cobertura" in flake, flake
    # no python toolchain / withPackages / inferred pytest for a go env.
    assert "withPackages" not in flake, flake
    assert "python" not in flake and "pytest" not in flake, flake
    assert flake.count("{") == flake.count("}"), flake  # balanced


def test_generate_flake_go_unknown_version_degrades_to_bare_go():
    from tools.runners.nix_provisioner import generate_flake

    # No toolchain pin (and an unmapped minor) must NOT emit a non-existent attr.
    flake = generate_flake({"language": "go", "verify_commands": ["go test ./..."]})
    assert "pkgs.go" in flake and "pkgs.gocover" not in flake, flake
    flake2 = generate_flake({"language": "go", "toolchain": {"go": "9.99"}})
    assert "pkgs.go\n" in flake2 or "pkgs.go " in flake2.rstrip(), flake2


# --- Python: pytest-cov + pyproject-derived deps (#615) ---


def test_python_flake_always_includes_pytest_and_cov_hyphen_safe():
    from tools.runners.nix_provisioner import generate_flake

    flake = generate_flake(
        {
            "language": "python",
            "toolchain": {"python": "3.12"},
            "verify_commands": ["pytest -q"],
            "provisioning": {"method": "nix", "generated": True},
        }
    )
    # pytest + pytest-cov (the runner always passes --cov), referenced as
    # quoted attrs so the hyphen doesn't parse as Nix subtraction.
    assert 'p."pytest"' in flake and 'p."pytest-cov"' in flake, flake
    assert "with p;" not in flake, flake  # switched to p."name" form
    assert flake.count("{") == flake.count("}"), flake


def test_deps_from_pyproject_maps_declared_deps_and_test_extras(tmp_path):
    from tools.runners.nix_provisioner import _deps_from_pyproject

    (tmp_path / "pyproject.toml").write_text(
        "[project]\n"
        'name = "orders-api"\n'
        'dependencies = ["fastapi>=0.115.0", "uvicorn[standard]>=0.32.0", '
        '"some-internal-lib>=1.0"]\n'
        "[project.optional-dependencies]\n"
        'dev = ["pytest>=8.3", "httpx>=0.27", "ruff>=0.7"]\n'
    )
    deps = _deps_from_pyproject(tmp_path)
    assert "fastapi" in deps and "uvicorn" in deps and "httpx" in deps
    assert "some-internal-lib" not in deps  # unmapped -> skipped, never guessed
    assert "ruff" not in deps  # not in the curated map


def test_generate_flake_seeds_sut_deps_from_pyproject(tmp_path):
    from tools.runners.nix_provisioner import generate_flake

    (tmp_path / "pyproject.toml").write_text(
        "[project]\n"
        'name = "orders-api"\n'
        'dependencies = ["fastapi>=0.115.0", "uvicorn[standard]>=0.32.0"]\n'
        "[project.optional-dependencies]\n"
        'dev = ["httpx>=0.27"]\n'
    )
    flake = generate_flake(
        {
            "language": "python",
            "toolchain": {"python": "3.12"},
            "verify_commands": ["pytest -q"],
            "provisioning": {"method": "nix", "generated": True},
        },
        project_dir=tmp_path,
    )
    assert 'p."fastapi"' in flake and 'p."uvicorn"' in flake and 'p."httpx"' in flake, (
        flake
    )
    assert flake.count("{") == flake.count("}"), flake


def test_deps_from_pyproject_missing_file_is_empty(tmp_path):
    from tools.runners.nix_provisioner import _deps_from_pyproject

    assert _deps_from_pyproject(tmp_path) == []
