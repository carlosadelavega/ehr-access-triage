"""Reduce a raw Synthea CSV export to the few columns the generator reads.

The raw export is several GB at 3,500 patients, most of it claims. The
generator needs living patients, their recent encounters and which of those
encounters had medications, imaging or lab results.
"""
import argparse
from pathlib import Path

import pandas as pd


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("raw_dir", type=Path)
    ap.add_argument("out_dir", type=Path)
    ap.add_argument("--reference-date", required=True, help="YYYYMMDD, as passed to Synthea")
    ap.add_argument("--lookback-years", type=int, default=10)
    args = ap.parse_args()
    args.out_dir.mkdir(parents=True, exist_ok=True)
    ref = pd.Timestamp(args.reference_date)
    cutoff = ref - pd.DateOffset(years=args.lookback_years)

    patients = pd.read_csv(args.raw_dir / "patients.csv", dtype=str)
    patients = patients[patients["DEATHDATE"].isna()]
    cols = ["Id", "BIRTHDATE", "FIRST", "LAST", "GENDER", "ADDRESS", "CITY", "ZIP", "LAT", "LON"]
    patients[cols].to_csv(args.out_dir / "patients.csv", index=False)

    enc = pd.read_csv(
        args.raw_dir / "encounters.csv",
        dtype=str,
        usecols=["Id", "START", "STOP", "PATIENT", "ENCOUNTERCLASS", "DESCRIPTION", "REASONDESCRIPTION"],
    )
    start = pd.to_datetime(enc["START"], utc=True).dt.tz_localize(None)
    enc = enc[(start >= cutoff) & enc["PATIENT"].isin(patients["Id"])]
    enc.to_csv(args.out_dir / "encounters.csv", index=False)
    keep = set(enc["Id"])

    meds = pd.read_csv(args.raw_dir / "medications.csv", dtype=str, usecols=["ENCOUNTER", "DESCRIPTION"])
    meds = meds[meds["ENCOUNTER"].isin(keep)].drop_duplicates()
    meds.to_csv(args.out_dir / "medications.csv", index=False)

    img = pd.read_csv(
        args.raw_dir / "imaging_studies.csv",
        dtype=str,
        usecols=["Id", "ENCOUNTER", "BODYSITE_DESCRIPTION", "MODALITY_DESCRIPTION"],
    )
    img = img[img["ENCOUNTER"].isin(keep)].drop_duplicates("Id")
    img["DESCRIPTION"] = img["MODALITY_DESCRIPTION"].fillna("Imaging") + ", " + img["BODYSITE_DESCRIPTION"].fillna("")
    img[["ENCOUNTER", "DESCRIPTION"]].to_csv(args.out_dir / "imaging.csv", index=False)

    lab_counts: dict[str, int] = {}
    reader = pd.read_csv(
        args.raw_dir / "observations.csv", dtype=str, usecols=["ENCOUNTER", "CATEGORY"], chunksize=500_000
    )
    for chunk in reader:
        chunk = chunk[(chunk["CATEGORY"] == "laboratory") & chunk["ENCOUNTER"].isin(keep)]
        for enc_id, n in chunk["ENCOUNTER"].value_counts().items():
            lab_counts[enc_id] = lab_counts.get(enc_id, 0) + int(n)
    labs = pd.DataFrame(sorted(lab_counts.items()), columns=["ENCOUNTER", "N_RESULTS"])
    labs.to_csv(args.out_dir / "labs.csv", index=False)

    print(f"patients {len(patients)}, encounters {len(enc)}, medications {len(meds)}, imaging {len(img)}, labs {len(labs)}")


if __name__ == "__main__":
    main()
