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
    get_error_message,
    validate_unique_terms,
    get_block_pattern
)


CONVERTIBLE_LANGUAGES = ['bash', 'sh', 'terminal', 'shell', 'console']

def extract_terms_from_source(source_lines):
    """
    Extract term names from shell/bash source lines before callout markers.
    
    Uses Specificity-First Logic:
    1. Placeholder patterns (__<value>__) - highest priority for doc placeholders
    2. Variable assignments (VAR=value) - most specific code construct
    3. Flags (--flag or --flag=value) - medium specificity
    4. Key-value pairs (key: value) - for embedded YAML/JSON
    5. AsciiDoc passthrough content - handle pass:c,a,q[...] syntax
    6. Commands (first token) - least specific
    
    Shell-specific handling:
    - Placeholders: __<value>__ <1> -> "__<value>__"
    - Variables: export VAR=value <1> -> "VAR"
    - Options/flags: --namespace=default <1> -> "--namespace"
    - Commands: oc create -f file.yaml <1> -> "oc"
    - Passthrough: pass:c,a,q[{url}] <1> -> use context or URL parameter
    - Comment-only callouts -> generic term
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
        
        # Find the position of the callout marker <N>, not just any <
        # This handles cases where placeholders like __<value>__ contain <
        marker_match = re.search(r'<\d+>', line)
        if marker_match:
            pre_marker = line[:marker_match.start()].strip()
        else:
            pre_marker = line[:line.find('<')].strip()
        
        # Remove prompt characters ($ or #) if present
        pre_marker = re.sub(r'^\s*[$#]\s*', '', pre_marker)
        
        # Remove YAML-style list prefix (- ) if present
        pre_marker = re.sub(r'^-\s+', '', pre_marker)
        
        # Edge case: Comment-only line
        if re.match(r'^#\s*$', pre_marker) or not pre_marker:
            terms[marker_num] = f"note-{marker_num}"
            continue
        
        # SPECIFICITY-FIRST LOGIC: Check patterns from most specific to least specific
        
        # Priority 0: AsciiDoc passthrough syntax (pass:c,a,q[...])
        # This is used for URLs and special content - extract the meaningful part
        passthrough_match = re.search(r'pass:[a-z,]+\[([^\]]+)\]', pre_marker)
        if passthrough_match:
            passthrough_content = passthrough_match.group(1)
            # Check if there's a URL parameter after the passthrough (like ?param=value)
            url_param_match = re.search(r'\?([a-zA-Z_][a-zA-Z0-9_]*)', line)
            if url_param_match:
                # If there's a URL parameter, use the full URL structure as term
                if '#' in line:
                    terms[marker_num] = f"pass:c,a,q[{{prod-url}}]"
                else:
                    terms[marker_num] = passthrough_content
            else:
                terms[marker_num] = f"pass:c,a,q[{passthrough_content}]"
            continue
        
        # Priority 0.5: URL fragments and comments in URL blocks
        # Handle lines like #https://... or ?param=value
        if pre_marker.startswith('#http') or pre_marker.startswith('http'):
            terms[marker_num] = f"#https://..."
            continue
        
        if pre_marker.startswith('?'):
            terms[marker_num] = f"?<optional_parameters>"
            continue
        
        # Priority 1: Standalone placeholders (__<placeholder>__)
        # When a placeholder is the primary content (not part of key:value or flag)
        # This handles lines like: $ __<git_repository_url>__
        # But NOT: key: __<value>__ (handled by key-value pattern)
        # Check if placeholder is at the start or is the main content
        if not re.search(r'^[a-zA-Z0-9_\-\.]+\s*:', pre_marker.strip()):
            # Not a key:value pattern, check for standalone placeholder
            placeholder_match = re.search(r'(__<[^>]+>__)', pre_marker)
            if placeholder_match:
                # Verify it's the main content (not just a value in an assignment)
                if not re.search(r'\b[A-Z_][A-Z0-9_]*\s*=', pre_marker):
                    terms[marker_num] = placeholder_match.group(1)
                    continue
        
        # Priority 1.5: Variable assignments
        # Patterns: VAR=value, export VAR=value, VAR="value"
        # Extract: VAR
        var_match = re.search(r'\b([A-Z_][A-Z0-9_]*)\s*=', pre_marker)
        if var_match:
            term = var_match.group(1)
            terms[marker_num] = term
            continue
        
        # Priority 2: Flags/options with placeholder arguments
        # If a flag is followed by a placeholder like __<value>__, extract the placeholder
        # since that's what the callout is explaining
        # Patterns: --flag __<value>__, --flag=__<value>__
        flag_with_placeholder = re.search(r'(?:^|\s)-{1,2}[a-zA-Z][a-zA-Z0-9_\-]*\s+(__<[^>]+>__)', pre_marker)
        if flag_with_placeholder:
            terms[marker_num] = flag_with_placeholder.group(1)
            continue
        
        # Priority 2.5: Standalone flags (without placeholder arguments)
        # Patterns: --flag, --flag=value, -f
        # Extract: just the flag name
        flag_match = re.search(r'(?:^|\s)(-{1,2}[a-zA-Z][a-zA-Z0-9_\-]*)', pre_marker)
        if flag_match:
            term = flag_match.group(1)
            # Prefer longest match (--flag over -f)
            double_dash = re.search(r'(?:^|\s)(--[a-zA-Z][a-zA-Z0-9_\-]*)', pre_marker)
            if double_dash:
                term = double_dash.group(1)
            terms[marker_num] = term
            continue
        
        # Priority 3: Key-value pairs (for echo'd YAML/JSON in shell blocks)
        # Patterns: key: value, name: "demo", secret: "..."
        # But NOT for lines that start with [ which are config sections
        # Extract: key (not the value)
        if not pre_marker.strip().startswith('['):
            kv_match = re.search(r'^([a-zA-Z0-9_\-\.]+)\s*:', pre_marker.strip())
            if kv_match:
                term = kv_match.group(1)
                terms[marker_num] = term
                continue
        
        # Priority 3.5: Config section headers like [section.name]
        # Extract the full section header
        section_match = re.search(r'(\[[^\]]+\])', pre_marker)
        if section_match:
            term = section_match.group(1)
            terms[marker_num] = term
            continue
        
        # Priority 4: Quoted strings (file paths, URLs, values)
        # For: "http://example.com/" -> extract domain
        # For: file://... or "value" -> extract meaningful part
        quoted_match = re.search(r'["\']([^"\']+)["\']', pre_marker)
        if quoted_match:
            quoted_value = quoted_match.group(1)
            # For URLs, extract domain
            if quoted_value.startswith(('http://', 'https://')):
                domain_match = re.search(r'https?://(?:www\.)?([^/:]+)', quoted_value)
                if domain_match:
                    term = domain_match.group(1)
                else:
                    term = "url"
                terms[marker_num] = term
                continue
            # For short alphanumeric values, use as-is
            if len(quoted_value) < 30 and re.match(r'^[\w\-\.]+$', quoted_value):
                terms[marker_num] = quoted_value
                continue
        
        # Priority 5: Commands (least specific - fallback)
        # Extract first meaningful token (command name)
        # For: "oc create -f file.yaml" -> "oc"
        # For: "export CLUSTER_NAME=..." -> "export" (but this should have matched var pattern above)
        # Remove common command prefixes like "export", "sudo"
        clean_line = re.sub(r'^\s*(export|sudo|sh|bash)\s+', '', pre_marker)
        command_match = re.match(r'([a-zA-Z0-9_\-\.]+)', clean_line)
        if command_match:
            term = command_match.group(1)
            terms[marker_num] = term
            continue
        
        # Fallback: Look for any placeholder-like pattern as last resort
        # This catches cases like __value__ without angle brackets
        fallback_placeholder = re.search(r'(__[a-zA-Z0-9_]+__)', pre_marker)
        if fallback_placeholder:
            terms[marker_num] = fallback_placeholder.group(1)
            continue
        
        # Last resort fallback: If nothing matches, use generic term
        terms[marker_num] = f"parameter-{marker_num}"
    
    # Validate that all extracted terms are unique
    validate_unique_terms(terms)
    
    return terms

def convert_shell_block(full_match, terms, cleaned_source, debug=False):
    """Convert a shell/bash code block with callouts to definition list format"""
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
    """Process a single AsciiDoc file for shell callouts"""
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
    
    pattern = get_block_pattern()
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
            cleaned_source_lines = [clean_source_line(line, 'shell') for line in source_lines]
            cleaned_source = '\n'.join(cleaned_source_lines)
            new_block, complete = convert_shell_block(match, terms, cleaned_source, debug)
            
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
    """Main entry point for shell/bash conversion"""
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
    
    print(f"Processing {len(file_list)} files for shell/bash callout conversion...")
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
    
    print(f"\n--- Shell/Bash Conversion Summary ---")
    print(f"Files Converted: {converted_count}")
    print(f"Warnings (incomplete): {warnings_count}")
    print(f"Files Skipped/Errors: {len(errors)}")
    if errors:
        print("Skipped files:", ', '.join(errors))

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Convert AsciiDoc shell/bash callouts to definition lists.")
    parser.add_argument('list_file', help='Path to file list (TXT or JSON from classifier)')
    parser.add_argument('--debug', action='store_true', help='Enable debug prints for term/definition processing')
    args = parser.parse_args()
    main(args.list_file, args.debug)

