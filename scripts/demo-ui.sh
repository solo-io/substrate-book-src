#!/usr/bin/env bash
# Presenter helpers for the live demo scripts: scripts/live-demo.sh here, and
# talk scripts that source it from a book-src checkout. Source it after the
# option parsing, so AUTO is set.
#
#   run "<command>"   type the command, wait for a key, run it, indent its
#                     output, then wait for a key again, so nothing else
#                     prints until you have talked about the output
#   prompt_cmd "<c>"  type a command at the prompt without running it
#   after_output      wait for a key after output you showed yourself
#   say / takeaway    narration, and a step's closing line
#   pause [label]     wait for a key (q quits). A bare pause right after
#                     run's own wait is skipped, so it never costs two keys
#   spin <msg> <pid>  a spinner until pid exits
#   hr [color]        a rule across the screen
#
# AUTO=N sleeps N seconds at each wait instead of reading a key, and turns off
# the typing effect, as does DEMO_NO_TYPING=1. DEMO_RUN_FILTER names a command
# that run pipes each command's output through (default cat).

ESC=$'\033'
RST="${ESC}[0m" B="${ESC}[1m" DIM="${ESC}[2m"
ACC="${ESC}[38;5;213m"  # magenta: step chrome
CYN="${ESC}[38;5;81m"   # commands, YAML keys
YLW="${ESC}[38;5;221m"  # values worth reading
GRN="${ESC}[38;5;114m"  # success, takeaways, new policy lines
RED="${ESC}[38;5;203m"  # blocked
GRY="${ESC}[38;5;244m"
COLS="$(tput cols 2>/dev/null || echo 100)"
if ((COLS > 110)); then COLS=110; fi
DEMO_RUN_FILTER="${DEMO_RUN_FILTER:-cat}"
DEMO_WAITED=false

hr() { printf '%s%*s%s\n' "${1:-$GRY}" "${COLS}" '' "${RST}" | sed "s/ /─/g"; }

pause() {
  local label="${1:-next}" key=""
  if [[ "${DEMO_WAITED}" == true && $# -eq 0 ]]; then DEMO_WAITED=false; return; fi
  DEMO_WAITED=false
  if [[ -n "${AUTO:-}" ]]; then sleep "${AUTO}"; return; fi
  printf '\n  %s⏎ %s%s' "${DIM}" "${label}" "${RST}"
  read -rsn1 key </dev/tty || true
  printf '\r%*s\r' "${COLS}" ''
  if [[ "${key}" == q ]]; then echo "  bye"; exit 0; fi
}

after_output() {
  pause next
  DEMO_WAITED=true
}

typeout() {
  local text="$1" i
  if [[ -n "${AUTO:-}" || -n "${DEMO_NO_TYPING:-}" ]]; then printf '%s' "${text}"; return; fi
  for ((i = 0; i < ${#text}; i++)); do printf '%s' "${text:i:1}"; sleep 0.008; done
}

prompt_cmd() {
  DEMO_WAITED=false
  echo
  printf '  %s❯%s %s' "${GRN}" "${RST}" "${CYN}"
  typeout "$1"
  printf '%s' "${RST}"
}

say() { DEMO_WAITED=false; printf '  %s▌%s %s\n' "${ACC}" "${RST}" "$*"; }

takeaway() { DEMO_WAITED=false; printf '\n  %s%s✔ %s%s\n' "${GRN}" "${B}" "$*" "${RST}"; }

run() {
  local cmd="$1"
  prompt_cmd "${cmd}"
  pause run
  printf '\n'
  eval "${cmd}" 2>&1 | "${DEMO_RUN_FILTER}" | sed 's/^/    /' || true
  after_output
}

spin() {
  local message="$1" pid="$2" frames='⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏' i=0
  tput civis 2>/dev/null || true
  while kill -0 "${pid}" 2>/dev/null; do
    printf '\r  %s%s%s %s' "${ACC}" "${frames:i++%10:1}" "${RST}" "${message}"
    sleep 0.1
  done
  tput cnorm 2>/dev/null || true
  printf '\r%*s\r' "${COLS}" ''
}
