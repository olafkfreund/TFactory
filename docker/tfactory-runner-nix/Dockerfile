# tfactory-runner-nix — RFC-0005 Tier A execution substrate.
#
# A thin Nix base for the k8s Job-per-task runner. It carries ONLY nix + flakes;
# every actual toolchain (python, node, playwright + browsers, go, rust, ...)
# comes from the per-task `flake.nix` the planner declared in the contract
# `environment` block and the consumer generated. That is the whole point: one
# tiny reproducible base, no per-language image sprawl (retires the bake-it-in
# pain of the language runner images).
#
# A Job runs:  nix develop /work#default --command bash -c "<verify_commands>"
# against the co-mounted worktree at /work; the flake materializes the toolchain
# from cache.nixos.org (binary cache) and is GC'd with the Job.
#
# Proven 2026-06-17: a manifest-generated flake materialized in THIS base and ran
# a Playwright browser lane green, producing real screenshots (with the fonts +
# pinned-nixpkgs-rev fixes in nix_provisioner).
FROM nixos/nix:2.30.1

# flakes + the new CLI; filter-syscalls=false is required for nix to run inside
# an unprivileged container (proven). No build sandbox tricks needed because
# toolchains are fetched from the binary cache, not built.
RUN mkdir -p /etc/nix \
 && printf 'experimental-features = nix-command flakes\nfilter-syscalls = false\n' \
      >> /etc/nix/nix.conf

# #768: pre-realise the common Python toolchain closure INTO the image store.
# Under TFACTORY_NIX_IN_IMAGE every verify Job sources /nix from the image, so a
# per-task flake that resolves to these same store paths (same nixpkgs rev + same
# withPackages set as nix_provisioner emits) finds them present and skips the
# per-Job cache.nixos.org fetch that made a real spec exceed the verify deadline.
#
# `nix develop --profile` both realises the dev shell's inputs and pins them
# behind a profile gcroot, so the closure cannot be garbage-collected out of the
# final image layer. `mkShell` outputs are not `nix profile install`-able, which
# is why this is the right idiom. Kept in lockstep with
# nix_provisioner.DEFAULT_NIXPKGS by tests/docker/test_p0_nix_warmup.py.
COPY warmup /warmup
RUN nix develop --profile /nix/var/nix/profiles/tf-warmup \
      'path:/warmup#default' --command true

WORKDIR /work
