# Python standard-library API notes

## JSON input and output

Use `json.loads(text)` to parse a JSON string and `json.load(file_object)` to parse an open file. Use `json.dumps(value, ensure_ascii=False, indent=2)` to produce readable JSON text. Use `json.dump(value, file_object, ensure_ascii=False, indent=2)` to write JSON to a file.

## Paths and files

`pathlib.Path` provides object-oriented file handling. `Path(path).read_text(encoding="utf-8")` reads a text file. `Path(path).write_text(value, encoding="utf-8")` writes text. `Path.iterdir()` lists direct children and `Path.rglob(pattern)` recursively matches files. Check `Path.is_file()` before reading an entry.

## CSV files

Open CSV files with `newline=""` and an explicit encoding. `csv.DictReader(file_object)` yields each row as a dictionary. `csv.DictWriter(file_object, fieldnames=[...])` writes dictionaries after `writeheader()`.

## Aggregation

The `collections.Counter` class counts hashable values. `statistics.mean(values)` returns the arithmetic mean of non-empty numeric data. Handle an empty collection explicitly before calling aggregation functions.

## Command-line arguments

Use `argparse.ArgumentParser` for scripts with command-line inputs. Add positional or optional arguments with `add_argument`, then call `parse_args()`. Put executable behavior inside `main()` and call it under `if __name__ == "__main__":`.
