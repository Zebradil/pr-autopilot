{
  description = "Deterministic triage of automated dependency-update pull requests";

  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixpkgs-unstable";

  outputs =
    { nixpkgs, ... }:
    let
      inherit (nixpkgs) lib;
      forAllSystems =
        f:
        lib.genAttrs [
          "x86_64-linux"
          "aarch64-darwin"
        ] (system: f nixpkgs.legacyPackages.${system});
    in
    {
      packages = forAllSystems (pkgs: rec {
        default = pr-autopilot;
        pr-autopilot = pkgs.stdenvNoCC.mkDerivation {
          pname = "pr-autopilot";
          version = (lib.importJSON ./.release-please-manifest.json).".";
          src = lib.fileset.toSource {
            root = ./.;
            fileset = lib.fileset.unions [
              ./pr_autopilot.py
              ./prompts
              ./skills
              ./templates
              ./docs/manual.md
              ./tests
            ];
          };
          nativeBuildInputs = [ pkgs.makeWrapper ];
          buildInputs = [ pkgs.python3 ];
          doCheck = true;
          checkPhase = ''
            runHook preCheck
            python3 -m unittest discover -s tests -p 'test_*.py'
            runHook postCheck
          '';
          # The script reads prompts/ and skills/ relative to its own path, and the onboarding agent reads
          # templates/ and docs/ from there too, so everything lives together under share/.
          installPhase = ''
            runHook preInstall
            install -Dm755 pr_autopilot.py $out/share/pr-autopilot/pr_autopilot.py
            cp -r prompts skills templates $out/share/pr-autopilot/
            install -Dm644 docs/manual.md $out/share/pr-autopilot/docs/manual.md
            makeWrapper $out/share/pr-autopilot/pr_autopilot.py $out/bin/pr-autopilot \
              --suffix PATH : ${lib.makeBinPath [ pkgs.gh ]}
            runHook postInstall
          '';
          meta = {
            description = "Deterministic triage of Renovate and Dependabot pull requests";
            homepage = "https://github.com/Zebradil/pr-autopilot";
            mainProgram = "pr-autopilot";
          };
        };
      });
    };
}
