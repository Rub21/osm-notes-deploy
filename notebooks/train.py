"""Train and evaluate the notes classifier. Used by notebooks/train_colab.ipynb.

  python train.py --target category
  python train.py --target theme --from-csv labels.csv

Steps:
  1. Read the human labels from the labeler API (/api/labels.csv) or a file.
     One label per note: majority vote when several people labeled it.
  2. Build the model input: "[template] [poi_type] user_text".
  3. Split: 80% train / 20% test, stratified. Notes in languages outside
     TRAIN_LANGS go to test only (we measure them, we do not train on them).
  4. Train a simple baseline (TF-IDF + logistic regression) and SetFit.
  5. Write OUTPUT_DIR/run_<date>_<target>/:
       report.md        macro-F1, F1 per class, accuracy per language,
                        confusion matrix, comparison with the AI suggestion
       metrics.json     same numbers, for scripts
       errors.csv       test notes the model got wrong
       model/           the trained SetFit model
"""

import argparse
import datetime as dt
import json
import os
from collections import Counter

import numpy as np
import pandas as pd
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import accuracy_score, classification_report, f1_score
from sklearn.model_selection import train_test_split

def parse_args() -> argparse.Namespace:
    env = os.environ.get
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    p.add_argument("--target", choices=["category", "theme"], default=env("TARGET", "category"))
    p.add_argument("--base-model", default=env(
        "BASE_MODEL", "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"))
    p.add_argument("--train-langs", default=env("TRAIN_LANGS", "es,en,und"),
                   help="languages used for training; the rest are only measured")
    p.add_argument("--test-size", type=float, default=float(env("TEST_SIZE", "0.2")))
    p.add_argument("--min-per-class", type=int, default=int(env("MIN_PER_CLASS", "8")),
                   help="classes with fewer labels are left out and reported")
    p.add_argument("--iterations", type=int, default=int(env("SETFIT_ITERATIONS", "20")),
                   help="SetFit text pairs per example")
    p.add_argument("--epochs", type=int, default=int(env("SETFIT_EPOCHS", "1")))
    p.add_argument("--batch-size", type=int, default=int(env("BATCH_SIZE", "16")))
    p.add_argument("--seed", type=int, default=int(env("SEED", "42")))
    p.add_argument("--skip-setfit", action="store_true", help="only the baseline (fast check)")
    p.add_argument("--output", default=env("OUTPUT_DIR", "output"))
    p.add_argument("--from-csv", default=env("LABELS_CSV", LABELS_URL),
                   help="labels.csv file or URL (default: the labeler API)")
    return p.parse_args()


LABELS_URL = "https://osm-notes.rub21.com/api/labels.csv"

LABEL_COLS = ["note_id", "category", "theme", "claude_category", "claude_theme", "user_name",
              "lang", "template", "poi_type", "user_text"]


def read_labels_csv(src: str) -> pd.DataFrame:
    """labels.csv exported by the labeler (/api/labels.csv), from a path or URL."""
    if src.startswith(("http://", "https://")):
        import io
        import urllib.request
        with urllib.request.urlopen(src) as r:
            src = io.BytesIO(r.read())
    df = pd.read_csv(src, encoding="utf-8-sig", dtype=str, keep_default_na=False)
    df = df.replace({"": None})
    df["note_id"] = df["note_id"].astype(int)
    return df[LABEL_COLS]


def model_input(r) -> str:
    """Short template tags + the user's words."""
    tags = "".join(f"[{v}] " for v in (r.template, r.poi_type) if isinstance(v, str) and v)
    return (tags + " ".join(str(r.user_text or "").split())).strip()


def load_data(args) -> pd.DataFrame:
    df = read_labels_csv(args.from_csv)
    target = args.target
    df = df[df[target].notna() & (df[target] != "")]
    if target == "category":
        df = df[df[target] != "unsure"]  # "I don't know" teaches nothing
    if df.empty:
        return df

    # One label per note: majority vote; keep how much people agreed.
    def vote(g: pd.DataFrame) -> pd.Series:
        counts = Counter(g[target])
        label, n = counts.most_common(1)[0]
        first = g.iloc[0]
        return pd.Series({
            "label": label, "votes": len(g), "agreement": n / len(g),
            "claude": first[f"claude_{target}"], "lang": first["lang"],
            "template": first["template"], "poi_type": first["poi_type"],
            "user_text": first["user_text"]})

    out = df.groupby("note_id").apply(vote, include_groups=False).reset_index()
    out["text"] = [model_input(r) for r in out.itertuples(index=False)]
    return out


def split(df: pd.DataFrame, args):
    train_langs = {x.strip() for x in args.train_langs.split(",") if x.strip()}
    measure_only = df[~df["lang"].isin(train_langs)]
    pool = df[df["lang"].isin(train_langs)]
    counts = pool["label"].value_counts()
    small = sorted(counts[counts < args.min_per_class].index)
    pool = pool[~pool["label"].isin(small)]
    measure_only = measure_only[~measure_only["label"].isin(small)]
    train, test = train_test_split(pool, test_size=args.test_size, random_state=args.seed,
                                   stratify=pool["label"])
    test = pd.concat([test.assign(split="test"), measure_only.assign(split="measure_only")])
    return train, test, small


def baseline(train: pd.DataFrame, test: pd.DataFrame, seed: int) -> np.ndarray:
    """TF-IDF on character n-grams + logistic regression. Fast, no GPU.
    If SetFit does not beat this, the problem is in the data."""
    vec = TfidfVectorizer(analyzer="char_wb", ngram_range=(2, 5), min_df=2, sublinear_tf=True)
    clf = LogisticRegression(max_iter=2000, class_weight="balanced", random_state=seed)
    clf.fit(vec.fit_transform(train["text"]), train["label"])
    return clf.predict(vec.transform(test["text"]))


def train_setfit(train: pd.DataFrame, test: pd.DataFrame, args, model_dir: str) -> np.ndarray:
    from datasets import Dataset
    from setfit import SetFitModel, Trainer, TrainingArguments

    model = SetFitModel.from_pretrained(args.base_model, labels=sorted(train["label"].unique()))
    trainer = Trainer(
        model=model,
        args=TrainingArguments(batch_size=args.batch_size, num_epochs=args.epochs,
                               num_iterations=args.iterations, seed=args.seed,
                               report_to="none"),
        train_dataset=Dataset.from_pandas(train[["text", "label"]].reset_index(drop=True)),
    )
    trainer.train()
    model.save_pretrained(model_dir)
    return np.array(model.predict(test["text"].tolist()))


def scores(y_true, y_pred) -> dict:
    return {"n": int(len(y_true)),
            "accuracy": round(float(accuracy_score(y_true, y_pred)), 3),
            "macro_f1": round(float(f1_score(y_true, y_pred, average="macro", zero_division=0)), 3)}


def by_lang(test: pd.DataFrame, col: str) -> pd.DataFrame:
    rows = []
    for lang, g in test.groupby("lang"):
        s = scores(g["label"], g[col])
        rows.append({"lang": lang, "split": g["split"].iloc[0], **s})
    return pd.DataFrame(rows).sort_values("n", ascending=False)


def md_table(df: pd.DataFrame) -> str:
    cols = list(df.columns)
    lines = ["| " + " | ".join(map(str, cols)) + " |", "|" + "---|" * len(cols)]
    lines += ["| " + " | ".join(str(v) for v in row) + " |" for row in df.itertuples(index=False)]
    return "\n".join(lines)


def main() -> None:
    args = parse_args()
    stamp = dt.datetime.now().strftime("%Y%m%d_%H%M")
    run_dir = os.path.join(args.output, f"run_{stamp}_{args.target}")
    os.makedirs(run_dir, exist_ok=True)

    df = load_data(args)
    print(f"Labeled notes for '{args.target}': {len(df)}")
    if len(df) < 30 or df["label"].nunique() < 2:
        raise SystemExit("Not enough labels yet (need at least 30 notes and 2 classes).")
    print(df["label"].value_counts().to_string())

    train, test, small = split(df, args)
    in_test = test[test["split"] == "test"]
    print(f"Train: {len(train)} | test: {len(in_test)} | measure-only: {len(test) - len(in_test)}")
    if small:
        print(f"Left out (fewer than {args.min_per_class} labels): {', '.join(small)}")

    test = test.copy()
    test["pred_baseline"] = baseline(train, test, args.seed)
    models = ["baseline"]
    if not args.skip_setfit:
        print(f"Training SetFit on {args.base_model} ...", flush=True)
        test["pred_setfit"] = train_setfit(train, test, args, os.path.join(run_dir, "model"))
        models.append("setfit")
    best = models[-1]
    pred = f"pred_{best}"

    # Numbers on the stratified test split (training languages).
    t = test[test["split"] == "test"]
    summary = {m: scores(t["label"], t[f"pred_{m}"]) for m in models}
    with_ai = t[t["claude"].notna() & (t["claude"] != "")]
    if len(with_ai):
        summary["ai_suggestion"] = scores(with_ai["label"], with_ai["claude"])

    report = classification_report(t["label"], t[pred], output_dict=True, zero_division=0)
    per_class = pd.DataFrame([
        {"class": k, "precision": round(v["precision"], 2), "recall": round(v["recall"], 2),
         "f1": round(v["f1-score"], 2), "support": int(v["support"])}
        for k, v in report.items() if k not in ("accuracy", "macro avg", "weighted avg")])
    langs = by_lang(test, pred)
    confusion = pd.crosstab(t["label"], t[pred], rownames=["real"], colnames=["predicted"])

    errors = test[test["label"] != test[pred]]
    errors[["note_id", "lang", "split", "label", pred, "claude", "agreement", "text"]] \
        .to_csv(os.path.join(run_dir, "errors.csv"), index=False, encoding="utf-8-sig")
    test.to_csv(os.path.join(run_dir, "predictions.csv"), index=False, encoding="utf-8-sig")

    metrics = {"target": args.target, "base_model": args.base_model, "date": stamp,
               "train": len(train), "test": len(t), "measure_only": int(len(test) - len(t)),
               "left_out_classes": small, "summary": summary,
               "per_class": per_class.to_dict("records"), "per_lang": langs.to_dict("records"),
               "low_agreement_notes": int((df["agreement"] < 1).sum())}
    with open(os.path.join(run_dir, "metrics.json"), "w") as f:
        json.dump(metrics, f, indent=2, ensure_ascii=False)

    summary_df = pd.DataFrame([{"model": k, **v} for k, v in summary.items()])
    md = f"""# Run {stamp} · {args.target}

Base model: `{args.base_model}`
Train: {len(train)} · test: {len(t)} · measure-only languages: {len(test) - len(t)}
{f"Left out (fewer than {args.min_per_class} labels): {', '.join(small)}" if small else ""}

## Summary (test split)

`ai_suggestion` = how often the AI pre-label (claude_{args.target}) matched the human label.

{md_table(summary_df)}

## Per class ({best})

{md_table(per_class)}

## Per language ({best})

`measure_only` = languages not used for training.

{md_table(langs)}

## Confusion matrix ({best}, rows = real)

{md_table(confusion.reset_index())}

Errors: `errors.csv` ({len(errors)} notes).
"""
    with open(os.path.join(run_dir, "report.md"), "w") as f:
        f.write(md)
    print(md)
    print(f"Saved to {run_dir}")


if __name__ == "__main__":
    main()
