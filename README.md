# AsciiDoc Callouts Conversion Tool

Conversion tool to automatically convert AsciiDoc code block callouts to definition lists according to SSG (Style Guide) best practices.

## Overview

This tool automatically converts code blocks with callout markers (`<1>`, `<2>`, etc.) to modern definition list format, making documentation more maintainable and consistent.

**Before:**
```asciidoc
[source,yaml]
----
apiVersion: v1
kind: Pod
metadata:
  name: my-pod  <1>
  namespace: default  <2>
----
<1> Specifies the pod name
<2> Specifies the namespace
```

**After:**
```asciidoc
[source,yaml]
----
apiVersion: v1
kind: Pod
metadata:
  name: my-pod
  namespace: default
----

name:: Specifies the pod name

namespace:: Specifies the namespace
```

## Features

✅ **Multi-Language Support**: Handles YAML, JSON, bash/shell, Python, Go, and generic text/conf files  
✅ **Smart Classification**: Automatically detects which files can be safely converted  
✅ **Edge Case Handling**: Detects and flags complex cases for manual review  
✅ **World-Class Engineering**: DRY principles, shared utilities, zero code duplication  
✅ **Proactive Detection**: Catches issues BEFORE conversion (comment-only callouts, semantic placeholders)  
✅ **Dry-Run Mode**: Preview changes before applying  
✅ **Comprehensive Reports**: Detailed summaries of what was converted and what needs review  
✅ **Production Ready**: Extensive error handling and validation  

🌟 **See [WORLD_CLASS_ENHANCEMENTS.md](WORLD_CLASS_ENHANCEMENTS.md) for details on advanced features**  

## Supported Languages

| Language | Status | Converter |
|----------|--------|-----------|
| YAML/YML | ✅ Complete | `yaml_callout.py` |
| JSON | ✅ Complete | `json_callout.py` |
| Bash/Shell/Terminal | ✅ Complete | `shell_callout.py` |
| Python | ✅ Complete | `python_callout.py` |
| Go | ✅ Complete | `go_callout.py` |
| Text/Conf | ✅ Complete | `generic_callout.py` |

## Installation

1. Clone this repository:
```bash
git clone <repository-url>
cd callouts-conversion
```

2. Ensure Python 3.7+ is installed:
```bash
python3 --version
```

3. Make the orchestrator executable:
```bash
chmod +x callouts_orchestrator.py
```

## Quick Start

### Convert All Files in a Directory

```bash
./callouts_orchestrator.py /path/to/docs/
```

### Preview Changes (Dry Run)

```bash
./callouts_orchestrator.py /path/to/docs/ --dry-run
```

### Debug Mode

```bash
./callouts_orchestrator.py /path/to/docs/ --debug
```

## How It Works

The tool operates in three phases:

### Phase 1: Scan & Classify
- Recursively scans all `.adoc` files
- Detects code blocks with callouts
- Classifies each block as:
  - **Automatable**: Safe to convert automatically
  - **Manual Review**: Needs human attention (edge cases, complex patterns)

### Phase 2: Convert
- Routes each language to its specific converter
- Extracts terms from code (variables, functions, keys, etc.)
- Converts callout explanations to definition lists
- Validates completeness

### Phase 3: Report
- Generates `manual_review_TIMESTAMP.txt` with files needing attention
- Creates `conversion_summary_TIMESTAMP.json` with detailed statistics
- Displays summary in terminal

## Edge Cases Handled

The tool automatically detects and handles:

### Structural Issues
- ✅ **Multiple callouts on same line** → Manual review
- ✅ **Non-sequential markers** (`<1>`, `<3>`, `<5>`) → Manual review
- ✅ **Marker mismatches** (source vs. definitions) → Manual review
- ✅ **Duplicate markers** → Manual review

### Content Issues (World-Class Features)
- ✅ **Comment-only callouts** (`# <1>` with no code) → Handled gracefully
- ✅ **Semantic placeholders** (URL, USER, PASSWORD) → Flagged for refactoring
- ✅ **All-caps tokens** → Detected as semantic issues

### Already Converted
- ✅ **Already converted blocks** (has `::`) → Skip silently
- ✅ **Conditional directives** (`ifdef::`, `ifndef::`) → Manual review

### File System Issues
- ✅ **Empty files** → Skip
- ✅ **Binary files** → Skip
- ✅ **Symlinks** → Skip
- ✅ **Large files** (>50MB) → Skip
- ✅ **Encoding issues** → Try fallback encoding
- ✅ **Permission errors** → Log and continue

## Output Files

After running, you'll get:

1. **manual_review_TIMESTAMP.txt**
   - Files that need human review
   - Categorized by issue type
   - Includes reasons for flagging

2. **conversion_summary_TIMESTAMP.json**
   - Complete statistics
   - List of converted files
   - Error details
   - Skipped files breakdown

## Examples

### Example 1: Convert Current Directory
```bash
./callouts_orchestrator.py .
```

### Example 2: Convert Specific Module
```bash
./callouts_orchestrator.py /path/to/openshift-docs/modules/
```

### Example 3: Preview Before Converting
```bash
# First, see what would change
./callouts_orchestrator.py /path/to/docs/ --dry-run

# Review the output, then actually convert
./callouts_orchestrator.py /path/to/docs/
```

### Example 4: Debug Problematic Files
```bash
./callouts_orchestrator.py /path/to/docs/ --debug > debug.log 2>&1
```

## Language-Specific Behavior

### YAML/JSON
- Extracts keys from `key: value` pairs
- Preserves full paths (`spec.template.metadata.name`)
- Handles list items (lines starting with `-`)

### Shell/Bash/Terminal
- Extracts commands (`oc create`, `kubectl apply`)
- Handles variables (`VAR=value`)
- Preserves flags (`--namespace=default`)
- Strips prompt characters (`$`, `#`)

### Python
- Extracts function/class names
- Handles variable assignments
- Recognizes imports and method calls

### Go
- Extracts function definitions
- Handles type declarations
- Recognizes struct fields

### Text/Conf
- Generic key-value extraction
- Handles section headers `[section]`
- Flexible pattern matching

## Troubleshooting

### Issue: "Error: Required modules not found"
**Solution**: Ensure all converter files are in the same directory:
```bash
ls *.py
# Should show: callouts_orchestrator.py, granular_callput.py, yaml_callout.py, etc.
```

### Issue: "Permission denied"
**Solution**: Make the script executable:
```bash
chmod +x callouts_orchestrator.py
```

### Issue: "No files to convert automatically"
**Possible causes**:
1. No code blocks with callouts found
2. All blocks flagged for manual review
3. Wrong directory specified

**Solution**: Check the classification summary and review `manual_review_*.txt`

### Issue: "Incomplete conversion"
**Meaning**: The converter couldn't match all markers to terms  
**Solution**: File added to manual review list - inspect manually

### Issue: Encoding errors
**Note**: Tool automatically tries fallback encodings  
**If persists**: File may be binary or corrupted

## Advanced Usage

### Running Individual Converters

If you need to run a specific converter manually:

```bash
# YAML only
python3 yaml_callout.py automatable_lists.json

# JSON only
python3 json_callout.py automatable_lists.json

# Shell only
python3 shell_callout.py automatable_lists.json
```

### Custom Classification

Run the classifier separately to analyze files:

```bash
python3 granular_callput.py /path/to/docs/
```

This generates:
- `automatable_lists.json` - Files ready for conversion
- `manual_lists.json` - Files needing manual review

## File Structure

```
callouts-conversion/
├── callouts_orchestrator.py    # Main entry point (run this!)
├── granular_callput.py          # Classifier/analyzer
├── yaml_callout.py              # YAML converter
├── json_callout.py              # JSON converter
├── shell_callout.py             # Bash/shell converter
├── python_callout.py            # Python converter
├── go_callout.py                # Go converter
├── generic_callout.py           # Text/conf converter
├── callouts_conversion.py       # Legacy (obsolete)
└── README.md                    # This file
```

## Best Practices

1. **Always run with `--dry-run` first** to preview changes
2. **Review the manual_review list** before considering those files
3. **Run on a git repository** so you can easily revert if needed
4. **Test on a small subset first** before running on entire doc set
5. **Keep backups** of important documentation

## Safety Features

- **No destructive operations** without successful conversion
- **Rollback on incomplete conversions** (file not modified)
- **Detailed logging** of all skipped files
- **Validation** of marker completeness before writing
- **Graceful error handling** - one file error doesn't stop entire run

## Performance

- **Fast**: Processes hundreds of files in seconds
- **Memory efficient**: Processes files one at a time
- **Skip optimization**: Quickly skips files without callouts

## Support

For issues, questions, or contributions:
1. Check the troubleshooting section above
2. Run with `--debug` to get detailed output
3. Review the `conversion_summary_*.json` file for specifics

## License

This program is free software, released under the terms of the [MIT license](LICENSE).

## Contributing

Contributions welcome! To add support for a new language:

1. Create a new converter file (e.g., `rust_callout.py`)
2. Implement the `extract_terms_from_source()` function for that language
3. Add the language to `granular_callput.py` SUPPORTED_LANGUAGES
4. Import and route in `callouts_orchestrator.py`


**Ready to convert?** Run `./callouts_orchestrator.py --help` for quick reference!

