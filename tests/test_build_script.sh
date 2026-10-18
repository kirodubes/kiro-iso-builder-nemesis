#!/bin/bash
set -euo pipefail
# Contract test: the build-the-iso.sh functions KIB relies on, run on a fake packages.x86_64
# with pacman stubbed. Uses the kiro-iso clone from $KIRO_ISO_DIR, else ~/KIRO-ISO-CALAMARES/kiro-iso-next.

ISO_DIR="${KIRO_ISO_DIR:-${HOME}/KIRO-ISO-CALAMARES/kiro-iso-next}"
SCRIPT="${ISO_DIR}/build-scripts/build-the-iso.sh"
[[ -f "${SCRIPT}" ]] || { echo "build-the-iso.sh not found: ${SCRIPT}"; exit 1; }

TMP="$(mktemp -d)"
trap 'rm -rf "${TMP}"' EXIT
FAILS=0
pass() { echo "  ok    $1"; }
fail() { echo "  FAIL  $1"; FAILS=$((FAILS + 1)); }

# The functions under test, lifted out of the real script.
for f in apply_editions apply_optional_edition_excludes check_group_drift; do
    grep -q "^${f}() {" "${SCRIPT}" || { echo "missing function in build-the-iso.sh: ${f}"; exit 1; }
    eval "$(sed -n "/^${f}() {/,/^}/p" "${SCRIPT}")"
done
log_info() { echo "INFO: $1" >> "${TMP}/log"; }
log_warn() { echo "WARN: $1" >> "${TMP}/log"; }
log_error() { echo "ERR: $1" >> "${TMP}/log"; }
log_section() { :; }
status_ok() { echo "OK: $1" >> "${TMP}/log"; }

# pacman stub: [extra] has xfwm4 thunar mousepad ristretto parole; xfce4-goodies also has a chaotic-only member.
pacman() {
    case "$*" in
        "-Slq extra") printf '%s\n' xfwm4 thunar mousepad ristretto parole ;;
        "-Sgq xfce4") printf '%s\n' xfwm4 thunar xfce4-panel-compiz ;;
        "-Sgq xfce4-goodies") printf '%s\n' mousepad ristretto parole ;;
        *) return 1 ;;
    esac
}

write_packages() {
    mkdir -p "${TMP}/repo/archiso"
    cat > "${TMP}/repo/archiso/packages.x86_64" <<'EOF'
base
### >>> EDITION-BLOCK xfce >>>
## group: xfce4
#xfwm4
#thunar
## group: xfce4-goodies optional
#mousepad
#ristretto
#parole
### <<< EDITION-BLOCK xfce <<<
### >>> EDITION-BLOCK qtile >>>
#kiro-qtile
#thunar
### <<< EDITION-BLOCK qtile <<<
EOF
}

shipped() { grep -cx "$1" "${PACKAGES_FILE}" || true; }

echo "apply_editions + optional excludes"
write_packages
SCRIPT_DIR="${TMP}"
PACKAGES_FILE="${TMP}/build.x86_64"
buildFolder="${TMP}/none"
cp "${TMP}/repo/archiso/packages.x86_64" "${PACKAGES_FILE}"
printf '%s\n' "# header" mousepad ristretto thunar > "${TMP}/package-selection.conf"
editions="xfce qtile"
default_session="xfce"
apply_editions
[[ "$(shipped mousepad)" == 0 ]] && pass "unticked optional mousepad left out" || fail "mousepad still ships"
[[ "$(shipped ristretto)" == 0 ]] && pass "unticked optional ristretto left out" || fail "ristretto still ships"
[[ "$(shipped parole)" == 1 ]] && pass "ticked optional parole ships" || fail "parole missing"
[[ "$(shipped thunar)" == 2 ]] && pass "fixed thunar ships from xfce4 and qtile" || fail "thunar count wrong"
[[ "$(shipped xfwm4)" == 1 ]] && pass "fixed xfwm4 ships" || fail "xfwm4 missing"
grep -q "^## group: xfce4-goodies optional$" "${PACKAGES_FILE}" && pass "group headers stay comments" || fail "header changed"
grep -q "left out: 2" "${TMP}/log" && pass "logs the count" || fail "no count logged"

echo "no selection file = everything ships"
cp "${TMP}/repo/archiso/packages.x86_64" "${PACKAGES_FILE}"
rm "${TMP}/package-selection.conf"
apply_editions
[[ "$(shipped mousepad)" == 1 ]] && pass "mousepad ships" || fail "mousepad missing"

echo "check_group_drift"
REPO_DIR="${TMP}/repo"
: > "${TMP}/log"
check_group_drift
grep -q "OK: Group xfce4 matches" "${TMP}/log" && pass "xfce4 matches; chaotic-only member ignored" || fail "xfce4 drift"
grep -q "OK: Group xfce4-goodies matches" "${TMP}/log" && pass "xfce4-goodies matches" || fail "goodies drift"
sed -i -e '/^#parole$/d' -e 's/^#ristretto$/#ristretto\n#gone-plugin/' "${TMP}/repo/archiso/packages.x86_64"
: > "${TMP}/log"
check_group_drift
grep -q "new members.*parole" "${TMP}/log" && pass "warns about a new upstream member" || fail "missed new member"
grep -q "no longer has.*gone-plugin" "${TMP}/log" && pass "warns about a dropped member" || fail "missed dropped member"

echo
if (( FAILS )); then echo "${FAILS} check(s) failed"; exit 1; fi
echo "all build script checks passed"
