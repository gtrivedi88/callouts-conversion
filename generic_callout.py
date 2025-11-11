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


CONVERTIBLE_LANGUAGES = ['text', 'conf', 'config', 'txt', 'plaintext']

def get_generic_block_pattern():
    return re.compile(
        r'(\[source,([a-zA-Z0-9_-]+).*?\n)'
        r'(\s*-{4,}\s*\n)'
        r'(.*?)\s*-{4,}\s*\n'
        r'(.*?)(?=\n={2,}|\n\[|\n\.|\n--|\n[A-Z]{3,}|\Z)',
        re.MULTILINE | re.DOTALL | re.IGNORECASE
    )

def extract_terms_from_source(source_lines):
    """
    Extract term names from generic text/config source lines before callout markers.
    
    Generic handling for text/conf files:
    - Key-value pairs: key=value <1> or key: value <1>
    - Simple lines: any text before marker
    - Config directives: DIRECTIVE value <1>
    - Section headers: [section] <1>
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
        
        pre_marker = line[:line.find('<')].strip()
        
        # Pattern 1: Key-value with = or :
        kv_match = re.search(r'([A-Za-z0-9_\-\.]+)\s*[=:]', pre_marker)
        if kv_match:
            term = kv_match.group(1)
            terms[marker_num] = term
            continue
        
        # Pattern 2: Section header [section]
        section_match = re.search(r'\[([^\]]+)\]', pre_marker)
        if section_match:
            term = section_match.group(1)
            terms[marker_num] = term
            continue
        
        # Pattern 3: First word/token (for directives)
        first_word_match = re.match(r'([A-Za-z0-9_\-\.\/]+)', pre_marker)
        if first_word_match:
            term = first_word_match.group(1)
            terms[marker_num] = term
            continue
        
        # Fallback: use entire pre-marker content (up to 50 chars)
        if pre_marker:
            term = pre_marker[:50].strip()
            if term:
                terms[marker_num] = term
            else:
                raise ValueError(get_error_message('empty_term', f'Marker {marker_num} on line {line_num}'))
        else:
            raise ValueError(get_error_message('empty_term', f'Marker {marker_num} on line {line_num}'))
    
    return terms

def convert_generic_block(full_match, terms, cleaned_source, debug=False):
    """Convert a generic text/conf code block with callouts to definition list format"""
    header = full_match.group(1)
    open_delim = full_match.group(3)
    new_source = f"{header}{open_delim}{cleaned_source}\n----"
    def_content = full_match.group(5)
    
    if debug:
        print("Debug: def_content raw:", repr(def_content))
        markers = re.findall(r'<(\d+)>', def_content)
        print(f"Debug: Markers found in defs: {markers}")
    
    new_defs, complete = parse_and_replace_definitions(def_content, terms, use_backticks=True, debug=debug)
    return f"{new_source}\n\n{new_defs}\n\n", complete

def process_file(file_path, debug=False):
    """Process a single AsciiDoc file for generic text/conf callouts"""
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()
    except UnicodeDecodeError:
        try:
            with open(file_path, 'r', encoding='latin-1') as f:
                content = f.read()
        except Exception as e:
            print(f"Error: Cannot read {file_path}: {e}", file=sys.stderr)
            return False, 0
    except Exception as e:
        print(f"Error: Cannot read {file_path}: {e}", file=sys.stderr)
        return False, 0
    
    pattern = get_generic_block_pattern()
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
                continue
            
            if debug:
                print(f"Debug: Extracted terms: {terms}")
            
            # Clean markers and potential comments
            cleaned_source_lines = []
            for line in source_lines:
                cleaned_line = re.sub(r'<(\d+)>', '', line).rstrip()
                # Handle # comments
                if re.search(r'#\s*<\d+>\s*$', line):
                    cleaned_line = re.sub(r'#\s*<\d+>\s*$', '', cleaned_line).rstrip()
                cleaned_line = re.sub(r'\s+#\s*$', '', cleaned_line)
                cleaned_source_lines.append(cleaned_line)
            
            cleaned_source = '\n'.join(cleaned_source_lines)
            new_block, complete = convert_generic_block(match, terms, cleaned_source, debug)
            
            if not complete:
                incomplete = True
                if debug:
                    print(f"Debug: Incomplete conversion for block in {file_path}")
            
            modified_content = modified_content.replace(match.group(0), new_block, 1)
            converted_blocks += 1
            
        except ValueError as e:
            if debug:
                print(f"Warning: Skipping malformed block in {file_path}: {e}", file=sys.stderr)
            skipped_blocks += 1
            continue
        except Exception as e:
            print(f"Warning: Unexpected error in block in {file_path}: {e}", file=sys.stderr)
            skipped_blocks += 1
            continue
    
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
    
    return False, 0

def main(list_file_path, debug=False):
    """Main entry point for generic text/conf conversion"""
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
    
    print(f"Processing {len(file_list)} files for text/conf callout conversion...")
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
    
    print(f"\n--- Text/Conf Conversion Summary ---")
    print(f"Files Converted: {converted_count}")
    print(f"Warnings (incomplete): {warnings_count}")
    print(f"Files Skipped/Errors: {len(errors)}")
    if errors:
        print("Skipped files:", ', '.join(errors))

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Convert AsciiDoc text/conf callouts to definition lists.")
    parser.add_argument('list_file', help='Path to file list (TXT or JSON from classifier)')
    parser.add_argument('--debug', action='store_true', help='Enable debug prints for term/definition processing')
    args = parser.parse_args()
    main(args.list_file, args.debug)

