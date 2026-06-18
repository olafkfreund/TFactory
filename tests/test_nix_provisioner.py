

# --- RFC-0005 §3.2 Tier C (#66): content-addressed digest + tier resolver ---


def test_resolve_tier_maps_methods_and_defaults_to_nix():
    from tools.runners.nix_provisioner import resolve_tier
    assert resolve_tier({"provisioning": {"method": "nix"}}) == "nix"
    assert resolve_tier({"provisioning": {"method": "image"}}) == "catalog"
    assert resolve_tier({"provisioning": {"method": "build"}}) == "build"
    assert resolve_tier({"provisioning": {"method": "setup"}}) == "setup"
    assert resolve_tier({}) == "nix"               # default
    assert resolve_tier({"provisioning": {"method": "weird"}}) == "nix"  # degrade


def test_manifest_digest_is_content_addressed_by_environment_not_commands():
    from tools.runners.nix_provisioner import manifest_digest
    base = {"language": "python", "toolchain": {"python": "3.12"},
            "system_packages": ["go", "cmake"], "network": "restricted",
            "provisioning": {"method": "nix"}}
    same_env_diff_cmds = dict(base, verify_commands=["pytest -q tests/api"],
                              system_packages=["cmake", "go"])  # reordered
    diff_toolchain = {"language": "rust", "toolchain": {"rust": "1.90"},
                      "provisioning": {"method": "build"}}
    assert manifest_digest(base) == manifest_digest(same_env_diff_cmds)  # cache hit
    assert manifest_digest(base) != manifest_digest(diff_toolchain)
    d = manifest_digest(base)
    assert len(d) == 16 and d == manifest_digest(base)  # stable
