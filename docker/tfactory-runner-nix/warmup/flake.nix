{
  # Warm-up flake (#768). Realised ONCE at image build time so its closure —
  # python + pytest + pytest-cov + pip and their build deps (stdenv, gcc-wrapper)
  # — lands in the image's /nix/store. Under TFACTORY_NIX_IN_IMAGE every verify
  # Job sources /nix from the image, so a per-task flake that resolves to these
  # same paths finds them already present and skips the cache.nixos.org fetch
  # that made a real spec exceed the verify deadline (S x (3 + mutants) Jobs each
  # cold-fetching the identical closure).
  #
  # This must stay in lockstep with what nix_provisioner.generate_flake emits for
  # the common Python case: the SAME nixpkgs rev (nix_provisioner.DEFAULT_NIXPKGS)
  # and the SAME withPackages set. A drift between the two silently reverts to the
  # slow cold-fetch (no correctness bug, just no speed-up), so
  # tests/docker/test_p0_nix_warmup.py pins them together.
  description = "tfactory-runner-nix warm-up closure (#768)";
  inputs.nixpkgs.url = "github:NixOS/nixpkgs/567a49d1913ce81ac6e9582e3553dd90a955875f";
  outputs = { self, nixpkgs }:
    let
      system = "x86_64-linux";
      pkgs = import nixpkgs { inherit system; };
    in
    {
      devShells.${system}.default = pkgs.mkShell {
        packages = [
          (pkgs.python313.withPackages (p: [ p."pytest" p."pytest-cov" p."pip" ]))
        ];
      };
    };
}
