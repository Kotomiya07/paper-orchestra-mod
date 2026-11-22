{
  description = "PaperOrchestra Python and LaTeX development shell";

  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-24.11";

  outputs = { nixpkgs, ... }:
    let
      systems = [ "x86_64-linux" "aarch64-linux" "x86_64-darwin" "aarch64-darwin" ];
    in {
      devShells = nixpkgs.lib.genAttrs systems (system:
        let
          pkgs = import nixpkgs { inherit system; };
          latex = pkgs.texlive.combine {
            inherit (pkgs.texlive) scheme-small amsfonts amsmath bm booktabs
              caption enumitem eso-pic etoolbox fancyhdr graphics hyperref latexmk
              natbib psnfss silence url xcolor xspace;
          };
          libraries = with pkgs; [ zlib openssl ] ++ lib.optionals stdenv.isLinux [
            stdenv.cc.cc.lib libGL glib
          ];
        in {
          default = pkgs.mkShell {
            packages = with pkgs; [ python311 uv pkg-config latex ];
            buildInputs = libraries;
            LD_LIBRARY_PATH = pkgs.lib.makeLibraryPath libraries;
          };
        });
    };
}
