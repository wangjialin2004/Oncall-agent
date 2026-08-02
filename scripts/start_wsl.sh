#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
REPO_ROOT="$(cd -- "${SCRIPT_DIR}/.." && pwd -P)"
FRONTEND_DIR="${REPO_ROOT}/frontend"
FRONTEND_LOG="${REPO_ROOT}/frontend-vite.log"

API_URL="http://127.0.0.1:9900"
API_READINESS_URL="${API_URL}/health/readiness"
MILVUS_HEALTH_URL="http://127.0.0.1:9091/healthz"
FRONTEND_URL="http://127.0.0.1:5173/"
FRONTEND_PORT="5173"
FRONTEND_HOST="0.0.0.0"

WSL_START_SKIP_FRONTEND="${WSL_START_SKIP_FRONTEND:-false}"
WSL_START_ALLOW_DEGRADED="${WSL_START_ALLOW_DEGRADED:-false}"

die() {
	printf 'ERROR: %s\n' "$*" >&2
	exit 1
}

require_command() {
	local command_name="$1"
	command -v "${command_name}" >/dev/null 2>&1 || die "required command not found: ${command_name}"
}

validate_bool() {
	local variable_name="$1"
	local variable_value="$2"
	case "${variable_value}" in
		true|false) ;;
		*) die "${variable_name} must be true or false (got: ${variable_value})" ;;
	esac
}

load_node_toolchain() {
	local nvm_script="${NVM_DIR:-${HOME}/.nvm}/nvm.sh"
	if [[ -r "${nvm_script}" ]]; then
		set +u
		# shellcheck source=/dev/null
		source "${nvm_script}"
		set -u
	fi

	local node_path=""
	local npm_path=""
	node_path="$(command -v node || true)"
	npm_path="$(command -v npm || true)"
	[[ -n "${node_path}" && -n "${npm_path}" ]] || die "WSL node/npm unavailable; install Node 18+ or load NVM before starting"
	case "${node_path}:${npm_path}" in
		/mnt/*|/c/*|*:/mnt/*|*:/c/*)
			die "Windows node/npm detected (${node_path}, ${npm_path}); use the WSL toolchain"
			;;
	esac
}

http_status() {
	local url="$1"
	curl --noproxy '*' -sS --max-time 5 -o /dev/null -w '%{http_code}' "${url}" 2>/dev/null || true
}

http_body() {
	local url="$1"
	curl --noproxy '*' -sS --max-time 8 "${url}" 2>/dev/null || true
}

wait_for_http() {
	local url="$1"
	local timeout_seconds="$2"
	local attempts=$((timeout_seconds * 2))
	local attempt
	for ((attempt = 1; attempt <= attempts; attempt++)); do
		if [[ "$(http_status "${url}")" == "200" ]]; then
			return 0
		fi
		sleep 0.5
	done
	return 1
}

port_is_listening() {
	local port="$1"
	ss -ltnH 2>/dev/null | awk -v port=":${port}" '$4 ~ port "[[:space:]]" { found = 1 } END { exit(found ? 0 : 1) }'
}

check_layout() {
	[[ -x "${REPO_ROOT}/.venv/bin/python" ]] || die "missing ${REPO_ROOT}/.venv/bin/python; create the WSL virtualenv first"
	if [[ "${WSL_START_SKIP_FRONTEND}" == "false" ]]; then
		[[ -x "${FRONTEND_DIR}/node_modules/.bin/vite" ]] || die "missing frontend dependencies; run npm --prefix frontend install in WSL"
	fi
}

start_backend() {
	printf 'Starting Milvus and backend services from %s\n' "${REPO_ROOT}"
	make up
	if ! wait_for_http "${MILVUS_HEALTH_URL}" 30; then
		printf 'Milvus health endpoint did not return 200: %s\n' "${MILVUS_HEALTH_URL}" >&2
		die "inspect Docker/Milvus status before retrying"
	fi

	make start
	if wait_for_http "${API_READINESS_URL}" 45; then
		printf 'FastAPI readiness: %s\n' "$(http_body "${API_READINESS_URL}")"
		return 0
	fi

	local readiness_status
	readiness_status="$(http_status "${API_READINESS_URL}")"
	if [[ "${WSL_START_ALLOW_DEGRADED}" == "true" && "${readiness_status}" =~ ^5 ]]; then
		printf 'WARNING: FastAPI readiness is degraded (HTTP %s): %s\n' "${readiness_status}" "$(http_body "${API_READINESS_URL}")" >&2
		return 0
	fi

	printf 'FastAPI readiness failed (HTTP %s): %s\n' "${readiness_status}" "$(http_body "${API_READINESS_URL}")" >&2
	printf 'Recent server.log:\n' >&2
	tail -n 80 "${REPO_ROOT}/server.log" 2>/dev/null || true
	die "FastAPI did not become ready"
}

start_frontend() {
	if [[ "${WSL_START_SKIP_FRONTEND}" == "true" ]]; then
		printf 'Frontend startup skipped (WSL_START_SKIP_FRONTEND=true)\n'
		return 0
	fi

	if [[ "$(http_status "${FRONTEND_URL}")" == "200" ]]; then
		printf 'Frontend already serving: %s\n' "${FRONTEND_URL}"
		return 0
	fi

	if port_is_listening "${FRONTEND_PORT}"; then
		die "port ${FRONTEND_PORT} is occupied but ${FRONTEND_URL} is not healthy; no process was terminated"
	fi

	printf 'Starting Vite from %s\n' "${FRONTEND_DIR}"
	setsid -f npm --prefix "${FRONTEND_DIR}" run dev -- --host "${FRONTEND_HOST}" --port "${FRONTEND_PORT}" \
		>>"${FRONTEND_LOG}" 2>&1 < /dev/null

	if ! wait_for_http "${FRONTEND_URL}" 30; then
		printf 'Recent frontend-vite.log:\n' >&2
		tail -n 80 "${FRONTEND_LOG}" 2>/dev/null || true
		die "Vite did not become reachable on ${FRONTEND_URL}"
	fi
	printf 'Frontend ready: %s\n' "${FRONTEND_URL}"
}

usage() {
	cat <<'USAGE'
Usage: scripts/start_wsl.sh [--help]

Start the local Milvus, Redis, MCP, FastAPI, and Vite services from WSL.

Environment:
  WSL_START_SKIP_FRONTEND=true    Start backend services only.
  WSL_START_ALLOW_DEGRADED=true   Allow a 5xx readiness response after startup.
USAGE
}

main() {
	if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
		usage
		return 0
	fi
	[[ "$#" -eq 0 ]] || die "unknown argument: $1 (use --help for usage)"

	cd -- "${REPO_ROOT}"
	validate_bool WSL_START_SKIP_FRONTEND "${WSL_START_SKIP_FRONTEND}"
	validate_bool WSL_START_ALLOW_DEGRADED "${WSL_START_ALLOW_DEGRADED}"

	require_command make
	require_command curl
	require_command ss
	require_command docker
	check_layout

	if [[ "${WSL_START_SKIP_FRONTEND}" == "false" ]]; then
		require_command setsid
		load_node_toolchain
	fi

	start_backend
	start_frontend

	printf '\nWSL stack is available:\n'
	if [[ "${WSL_START_SKIP_FRONTEND}" == "true" ]]; then
		printf '  App:  skipped by WSL_START_SKIP_FRONTEND=true\n'
	else
		printf '  App:  %s\n' "${FRONTEND_URL}"
	fi
	printf '  API:  %s\n' "${API_URL}"
	printf '  Docs: %s/docs\n' "${API_URL}"
}

main "$@"
