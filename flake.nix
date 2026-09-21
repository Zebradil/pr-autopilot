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
          # The script reads prompts/ relative to its own path, so both live together under share/.
          installPhase = ''
            runHook preInstall
            install -Dm755 pr_autopilot.py $out/share/pr-autopilot/pr_autopilot.py
            cp -r prompts $out/share/pr-autopilot/
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
