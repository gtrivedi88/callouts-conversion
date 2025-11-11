import sys
import re
import os
import json
import argparse
from collections import defaultdict
from converter_utils import (
    parse_and_replace_definitions,
    detect_edge_cases,
    clean_source_line,
    get_error_message
)


CONVERTIBLE_LANGUAGES = ['yaml', 'yml']

def get_yaml_block_pattern():
    return re.compile(
        r'(\[source,([a-zA-Z0-9_-]+).*?\n)'
        r'(\s*-{4,}\s*\n)'
        r'(.*?)\s*-{4,}\s*\n'
        r'(.*?)(?=\n={2,}|\n\[|\n\.|\n--|\n[A-Z]{3,}|\Z)',
        re.MULTILINE | re.DOTALL | re.IGNORECASE
    )

def extract_terms_from_source(source_lines):
    """
    Extract term names from YAML source lines before callout markers.
    
    Edge cases handled:
    - Multiple markers on same line (use first)
    - Duplicate markers (error)
    - Empty terms (error)
    - Full paths like 'path.to.value' (preserved)
    - List items starting with '-' (stripped)
    - Comment-only callouts (generic term)
    - Semantic placeholders (all-caps tokens - flagged)
    """
    # First, check for edge cases
    has_issues, issue_type, issue_desc = detect_edge_cases(source_lines)
    if has_issues:
        raise ValueError(get_error_message(issue_type, issue_desc))
    
    terms = {}
    marker_pattern = re.compile(r'<(\d+)>')
    
    for line_num, line in enumerate(source_lines, start=1):
        markers = marker_pattern.findall(line)
        if not markers:
            continue
        
        marker_num = int(markers[0])
        if marker_num in terms:
            raise ValueError(get_error_message('duplicate_marker', f'Marker {marker_num} on line {line_num}'))
        
        pre_marker = line[:line.find('<')]
        
        # Edge case: Comment-only line (just # with whitespace)
        if re.match(r'^#\s*$', pre_marker.strip()):
            # Use generic term for comment-only callouts
            terms[marker_num] = f"note-{marker_num}"
            continue
        
        colon_match = re.search(r'^(.*?)(?::|$)', pre_marker.strip())
        if colon_match:
            term = colon_match.group(1).strip()
            # Strip leading dashes (for list items), quotes, and whitespace
            # But preserve dots in paths like 'path.to.value'
            term = re.sub(r'^[-—\s]+|["\']|[-—\s]+$', '', term).strip()
            # Also strip comment markers if present
            term = re.sub(r'^#\s*', '', term).strip()
            
            if term:
                terms[marker_num] = term
            else:
                raise ValueError(get_error_message('empty_term', f'Marker {marker_num} on line {line_num}'))
    
    return terms

# parse_and_replace_definitions is now imported from converter_utils

def convert_yaml_block(full_match, terms, cleaned_source, debug=False):
    """Convert a YAML code block with callouts to definition list format"""
    header = full_match.group(1)
    open_delim = full_match.group(3)
    new_source = f"{header}{open_delim}{cleaned_source}\n----"
    def_content = full_match.group(5)
    
    if debug:
        print("Debug: def_content raw:", repr(def_content))
        markers = re.findall(r'<(\d+)>', def_content)
        print(f"Debug: Markers found in defs: {markers}")
    
    # YAML keys don't need backticks, so use_backticks=False
    new_defs, complete = parse_and_replace_definitions(def_content, terms, use_backticks=False, debug=debug)
    return f"{new_source}\n\n{new_defs}\n\n", complete

def process_file(file_path, debug=False):
    """
    Process a single AsciiDoc file, converting YAML callouts to definition lists.
    
    Edge cases handled:
    - Files with no callouts (skip silently)
    - Mixed convertible/non-convertible languages (convert only YAML)
    - Incomplete conversions (rollback, return warning)
    - Malformed markers or definitions (skip block with warning)
    - Multiple YAML blocks in same file (process all)
    
    Returns: (success: bool, warnings: int)
    """
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()
    except UnicodeDecodeError:
        # Try fallback encoding
        try:
            with open(file_path, 'r', encoding='latin-1') as f:
                content = f.read()
        except Exception as e:
            print(f"Error: Cannot read {file_path}: {e}", file=sys.stderr)
            return False, 0
    except Exception as e:
        print(f"Error: Cannot read {file_path}: {e}", file=sys.stderr)
        return False, 0
    
    pattern = get_yaml_block_pattern()
    modified_content = content
    converted_blocks = 0
    incomplete = False
    skipped_blocks = 0
    
    for match in pattern.finditer(content):
        lang = match.group(2).lower()
        if lang not in CONVERTIBLE_LANGUAGES:
            continue
        
        raw_source = match.group(4)
        source_content = re.sub(r'\s*-{4,}\s*\n\s*$', '', raw_source, flags=re.MULTILINE).rstrip()
        source_lines = source_content.splitlines()
        
        try:
            terms = extract_terms_from_source(source_lines)
            if not terms:
                # Edge case: Block has no callouts, skip silently
                continue
            
            if debug:
                print(f"Debug: Extracted terms: {terms}")
            
            # Clean source lines using shared utility
            cleaned_source_lines = [clean_source_line(line, 'yaml') for line in source_lines]
            cleaned_source = '\n'.join(cleaned_source_lines)
            
            new_block, complete = convert_yaml_block(match, terms, cleaned_source, debug)
            if not complete:
                incomplete = True
                if debug:
                    print(f"Debug: Incomplete conversion for block in {file_path}")
            
            # Edge case: Use replace with count=1 to handle multiple identical blocks
            modified_content = modified_content.replace(match.group(0), new_block, 1)
            converted_blocks += 1
            
        except ValueError as e:
            # Edge case: Malformed block - skip with warning but continue processing file
            if debug:
                print(f"Warning: Skipping malformed block in {file_path}: {e}", file=sys.stderr)
            skipped_blocks += 1
            continue
        except Exception as e:
            # Edge case: Unexpected error - log and skip block
            print(f"Warning: Unexpected error in block in {file_path}: {e}", file=sys.stderr)
            skipped_blocks += 1
            continue
    
    # Edge case: Only write if we successfully converted at least one block AND no incomplete conversions
    if converted_blocks > 0 and not incomplete:
        try:
            with open(file_path, 'w', encoding='utf-8') as f:
                f.write(modified_content)
            if debug and skipped_blocks > 0:
                print(f"Debug: Converted {converted_blocks} blocks, skipped {skipped_blocks} blocks")
            return True, 0
        except Exception as e:
            print(f"Error: Cannot write to {file_path}: {e}", file=sys.stderr)
            return False, 0
    elif incomplete:
        if debug:
            print(f"Warning: Incomplete conversion in {file_path}; skipped write.", file=sys.stderr)
        return False, 1
    
    # Edge case: No blocks converted (either no YAML callouts or all skipped)
    return False, 0

def main(list_file_path, debug=False):
    if list_file_path.endswith('.json'):
        with open(list_file_path, 'r') as f:
            data = json.load(f)
        file_list = []
        for lang_group in data.values():
            file_list.extend(lang_group)
    else:
        with open(list_file_path, 'r') as f:
            file_list = [line.strip() for line in f if line.strip()]
    
    converted_count = 0
    warnings_count = 0
    errors = []
    
    print(f"Processing {len(file_list)} files for YAML callout conversion...")
    for file_path in sorted(file_list):
        if not os.path.exists(file_path):
            errors.append(f"{file_path} (does not exist)")
            continue
        success, warns = process_file(file_path, debug)
        if success:
            converted_count += 1
        warnings_count += warns
        if not success and os.path.exists(file_path) and "does not exist" not in file_path:
            errors.append(file_path)
    
    print(f"\n--- YAML Conversion Summary ---")
    print(f"Files Converted: {converted_count}")
    print(f"Warnings (incomplete): {warnings_count}")
    print(f"Files Skipped/Errors: {len(errors)}")
    if errors:
        print("Skipped files:", ', '.join(errors))

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Convert AsciiDoc YAML callouts to definition lists.")
    parser.add_argument('list_file', help='Path to file list (TXT or JSON from classifier)')
    parser.add_argument('--debug', action='store_true', help='Enable debug prints for term/definition processing')
    args = parser.parse_args()
    main(args.list_file, args.debug)