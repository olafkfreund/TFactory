{
  description = "TFactory portal-test harness — nix-provided Python Playwright + browsers (NixOS-safe, no pip browser download)";

  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";

  outputs = { self, nixpkgs }:
    let
      systems = [ "x86_64-linux" "aarch64-linux" ];
      forAll = f: nixpkgs.lib.genAttrs systems (s: f nixpkgs.legacyPackages.${s});
    in
    {
      devShells = forAll (pkgs:
        let
          # Python with Playwright + pyotp from nixpkgs — the C extensions and the
          # browser are linked against nix's own libs (no libstdc++ ImportError),
          # which is the whole reason pip's chromium fails on NixOS.
          py = pkgs.python3.withPackages (ps: [ ps.playwright ps.pyotp ]);
          # Minimal fontconfig so headless chromium can actually render text.
          fontconf = pkgs.makeFontsConf { fontDirectories = [ pkgs.dejavu_fonts ]; };
        in
        {
          default = pkgs.mkShell {
            packages = [ py pkgs.playwright-driver.browsers ];
            # Wire the nix-provided browsers; never download via pip/npx (the
            # proven nix_provisioner pattern, RFC-0005).
            PLAYWRIGHT_BROWSERS_PATH = "${pkgs.playwright-driver.browsers}";
            PLAYWRIGHT_SKIP_VALIDATE_HOST_REQUIREMENTS = "true";
            PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD = "1";
            FONTCONFIG_FILE = fontconf;
            shellHook = ''
              echo "tfactory-testing shell — python+playwright+browsers from nix"
              echo "run: python -m harness.run <pfactory|aifactory|tfactory|cfactory|all>"
            '';
          };
        });
    };
}
