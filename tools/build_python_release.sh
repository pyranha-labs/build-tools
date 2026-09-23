#!/usr/bin/env bash

# Build release files in isolated environment to ensure no dev files are included.

PROJECT=$(sed -n 's/^name = "\(.*\)"$/\1/p' pyproject.toml)
REPO="https://github.com/pyranha-labs/${PROJECT}.git"
IMAGE=python:3.10

# Pull out user args to modify default behavior.
while [[ $# -gt 0 ]]; do
  case $1 in
    --image)
      if [ -z "$2" ]; then echo "Must provide image version. Example: --image ${IMAGE}"; exit 1; fi
      IMAGE=$2
      shift
    ;;
    --repo)
      if [ -z "$2" ]; then echo "Must provide repo. Example: --repo https://github.com/pyranha-labs/project.git"; exit 1; fi
      REPO=$2
      shift
    ;;
  esac
  shift
done

# Force execution in docker to ensure reproducibility.
if [ ! -f /.dockerenv ]; then
  echo "Running in docker."
  docker run --rm -it -v `pwd`:/mnt/project -w /mnt/project ${IMAGE} bash -c "/mnt/project/build_python_release.sh --repo ${REPO}"
  exit 0
fi

# Ensure the whole script exits on failures.
set -e

git clone ${REPO} /mnt/clone
cd /mnt/clone
for req_file in requirements-full_dev.txt requirements-dev.txt requirements.txt; do
  if [ -f "${req_file}" ]; then
    echo "Installing ${req_file}..."
    pip install -r "${req_file}"
    echo "Installation of ${req_file} complete."
  else
    echo "${req_file} not found, skipping installation"
  fi
done
curl https://raw.githubusercontent.com/pyranha-labs/build-tools/refs/heads/main/python.mk -o python.mk
make wheel

dist="/mnt/project/dist"
if [ ! -d "${dist}" ]; then
  mkdir -vp "${dist}"
fi

echo "Removing old files:"
rm -v "${dist}/"* || echo No files to cleanup
echo
echo "Release files:"
cp dist/* "${dist}/"
ls "${dist}"
echo
echo "To push to testpypi:"
echo "python3 -m twine upload --repository testpypi dist/*"
echo
echo "To push to prod pypi:"
echo "python3 -m twine upload dist/*"
