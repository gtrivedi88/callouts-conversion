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


CONVERTIBLE_LANGUAGES = ['python', 'py']

def get_python_block_pattern():
    return re.compile(
        r'(\[source,([a-zA-Z0-9_-]+).*?\n)'
        r'(\s*-{4,}\s*\n)'
        r'(.*?)\s*-{4,}\s*\n'
        r'(.*?)(?=\n={2,}|\n\[|\n\.|\n--|\n[A-Z]{3,}|\Z)',
        re.MULTILINE | re.DOTALL | re.IGNORECASE
    )

def extract_terms_from_source(source_lines):
    """
    Extract term names from Python source lines before callout markers.
    
    Python-specific handling:
    - Variable assignments: var = value <1>
    - Function calls: function() <1>
    - Class definitions: class MyClass: <1>
    - Function definitions: def my_function(): <1>
    - Import statements: import module <1>
    - Method calls: obj.method() <1>
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
        
        # Pattern 1: Class definition
        class_match = re.search(r'class\s+([A-Za-z_][A-Za-z0-9_]*)', pre_marker)
        if class_match:
            term = class_match.group(1)
            terms[marker_num] = term
            continue
        
        # Pattern 2: Function definition
        func_def_match = re.search(r'def\s+([A-Za-z_][A-Za-z0-9_]*)', pre_marker)
        if func_def_match:
            term = func_def_match.group(1)
            terms[marker_num] = term
            continue
        
        # Pattern 3: Variable assignment
        var_match = re.search(r'([A-Za-z_][A-Za-z0-9_]*)\s*=', pre_marker)
        if var_match:
            term = var_match.group(1)
            terms[marker_num] = term
            continue
        
        # Pattern 4: Import statement
        import_match = re.search(r'(?:from\s+\S+\s+)?import\s+([A-Za-z_][A-Za-z0-9_]*(?:\s+as\s+[A-Za-z_][A-Za-z0-9_]*)?)', pre_marker)
        if import_match:
            term = import_match.group(1)
            terms[marker_num] = term
            continue
        
        # Pattern 5: Function/method call
        call_match = re.search(r'([A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*)\s*\(', pre_marker)
        if call_match:
            term = call_match.group(1)
            terms[marker_num] = term
            continue
        
        # Pattern 6: Dictionary key or attribute
        attr_match = re.search(r'([A-Za-z_][A-Za-z0-9_\.]*)', pre_marker)
        if attr_match:
            term = attr_match.group(1)
            terms[marker_num] = term
            continue
        
        # Fallback
        if pre_marker:
            # Try to extract any identifier
            fallback_match = re.search(r'([A-Za-z_][A-Za-z0-9_]*)', pre_marker)
            if fallback_match:
                term = fallback_match.group(1)
                terms[marker_num] = term
            else:
                raise ValueError(get_error_message('empty_term', f'Marker {marker_num} on line {line_num}'))
        else:
            raise ValueError(get_error_message('empty_term', f'Marker {marker_num} on line {line_num}'))
    
    return terms

def convert_python_block(full_match, terms, cleaned_source, debug=False):
    """Convert a Python code block with callouts to definition list format"""
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
    """Process a single AsciiDoc file for Python callouts"""
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
    
    pattern = get_python_block_pattern()
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
            
            # Clean source lines using shared utility
            cleaned_source_lines = [clean_source_line(line, 'python') for line in source_lines]
            cleaned_source = '\n'.join(cleaned_source_lines)
            new_block, complete = convert_python_block(match, terms, cleaned_source, debug)
            
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
    """Main entry point for Python conversion"""
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
    
    print(f"Processing {len(file_list)} files for Python callout conversion...")
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
    
    print(f"\n--- Python Conversion Summary ---")
    print(f"Files Converted: {converted_count}")
    print(f"Warnings (incomplete): {warnings_count}")
    print(f"Files Skipped/Errors: {len(errors)}")
    if errors:
        print("Skipped files:", ', '.join(errors))

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Convert AsciiDoc Python callouts to definition lists.")
    parser.add_argument('list_file', help='Path to file list (TXT or JSON from classifier)')
    parser.add_argument('--debug', action='store_true', help='Enable debug prints for term/definition processing')
    args = parser.parse_args()
    main(args.list_file, args.debug)

