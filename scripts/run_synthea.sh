#!/usr/bin/env bash
# Generate the synthetic population and reduce it to the files the generator
# reads. Needs Java 11 or newer. Takes roughly half an hour for 3,500 patients.
#
# Seed, population and reference date are fixed so the population can be
# rebuilt. Keep them in step with the synthea block in config/calibration.yaml.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
JAR="$ROOT/vendor/synthea-with-dependencies.jar"
RAW="$ROOT/data/synthea/raw"
SLIM="$ROOT/data/synthea/slim"
SEED=42
POPULATION=3500
REFERENCE_DATE=20261101

[ -f "$JAR" ] || "$ROOT/scripts/fetch_synthea.sh"
rm -rf "$RAW"
java -jar "$JAR" \
  -s "$SEED" -cs "$SEED" -r "$REFERENCE_DATE" -e "$REFERENCE_DATE" -p "$POPULATION" \
  --exporter.csv.export=true \
  --exporter.fhir.export=false \
  --exporter.hospital.fhir.export=false \
  --exporter.practitioner.fhir.export=false \
  --generate.append_numbers_to_person_names=false \
  --exporter.baseDirectory="$RAW/" \
  Massachusetts Springfield
python3 "$ROOT/scripts/slim_synthea.py" "$RAW/csv" "$SLIM" --reference-date "$REFERENCE_DATE"
echo "Slim population written to $SLIM. The raw export in $RAW can be deleted."
