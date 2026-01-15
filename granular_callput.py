import sys
import re
import os
from collections import defaultdict
import json
import getopt
from converter_utils import get_block_pattern, normalize_language

SUPPORTED_LANGUAGES = ['yaml', 'json', 'yml', 'bash', 'sh', 'shell', 'terminal', 'console', 'text', 'conf', 'go', 'python']

def analyze_block(source_content, definition_block_content, debug=False):
    """
    Analyze a code block to determine if it's automatable or needs manual review.
    
    Edge cases handled:
    - Multiple callouts on same line
    - Duplicate/shared markers
    - Marker count mismatches
    - Already converted definition lists (::)
    - Non-sequential markers (<1>, <3>, <5> etc.)
    - Nested or conditional callouts
    """
    # Edge case: Check if already converted (has :: but no < markers)
    if '::' in definition_block_content and '<' not in definition_block_content:
        return 'manual_already_converted', 'Block appears already converted to definition list.'
    
    # Edge case: Multiple callouts on same line
    for line in source_content.splitlines():
        if len(re.findall(r'(\<\d+\>)', line)) > 1:
            return 'manual_multi_callout', 'Multiple callouts on a single line.'
    
    source_markers_list = re.findall(r'(\<\d+\>)', source_content)
    unique_source_markers = set(source_markers_list)
    
    # Edge case: Duplicate markers in source
    if len(source_markers_list) != len(unique_source_markers):
        return 'manual_ratio_mismatch', 'Shared marker definition detected in source code.'
    
    # Edge case: Check for sequential markers (should be 1, 2, 3... not 1, 3, 5...)
    if unique_source_markers:
        marker_nums = sorted([int(m.strip('<>')) for m in unique_source_markers])
        expected_sequence = list(range(1, len(marker_nums) + 1))
        if marker_nums != expected_sequence:
            return 'manual_non_sequential', f'Markers not sequential: {marker_nums} (expected {expected_sequence})'
    
    # Fixed: Capture only the number, use greedy (.*) for rest-of-line
    definition_lines = re.findall(r'^\s*\<([0-9]+)\>\s*(.*)', definition_block_content, re.MULTILINE)
    unique_def_markers = {f'<{m}>' for m, _ in definition_lines}
    
    # Edge case: Duplicate markers in definitions
    # BUT: Allow duplicates if they're in different conditional branches
    has_conditionals = any(keyword in definition_block_content for keyword in ['ifdef::', 'ifndef::', 'ifeval::'])
    def_marker_counts = defaultdict(int)
    for marker, _ in definition_lines:
        def_marker_counts[f'<{marker}>'] += 1
    has_duplicates = any(count > 1 for count in def_marker_counts.values())
    
    if has_duplicates and not has_conditionals:
        # True duplicates outside conditionals - can't fix
        return 'manual_ratio_mismatch', 'Duplicate definition marker found outside source block.'
    
    if has_duplicates and has_conditionals:
        # Duplicates in conditional branches - flag for manual restructuring
        # The converter can't handle this because definition lists can't have conditional terms
        duplicates = [m for m, c in def_marker_counts.items() if c > 1]
        return 'manual_conditional', f'Duplicate marker {duplicates[0]} in conditional branches - requires manual restructuring.'
    
    # Edge case: Mismatch between source and definition markers
    if unique_source_markers != unique_def_markers:
        if debug:
            print(f"  DEBUG: Source markers: {unique_source_markers}, Def markers: {unique_def_markers}")
        return 'manual_ratio_mismatch', 'Mismatch between unique source markers and definition markers.'
    
    # Note: Conditional handling is done in the duplicate markers check above
    # Basic conditionals without duplicates will pass through and be automatable
    
    # Plain block (no markers) - not an error, just no work to do
    if len(unique_source_markers) == 0:
        return 'plain_source_block', None
    
    return 'automatable', None

def process_file(file_path, debug=False):
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()
    except Exception:
        return {'status': 'error', 'blocks': []}
    
    pattern = get_block_pattern()
    all_blocks = []
    
    for match in pattern.finditer(content):
        # Normalize language - handles empty/missing language, defaults to 'shell'
        raw_lang = match.group(2) or ''
        language = normalize_language(raw_lang)
        if language not in SUPPORTED_LANGUAGES:
            continue  # Skip unsupported langs early
        source_content = match.group(4)
        definition_block_content = match.group(5)
        status, reason = analyze_block(source_content, definition_block_content, debug)
        all_blocks.append({
            'language': language,
            'status': status,
            'reason': reason,
            'has_markers': len(re.findall(r'(\<\d+\>)', source_content)) > 0
        })
    
    if not all_blocks:
        return {'status': 'no_callout_block', 'blocks': []}
    return {'status': 'processed', 'blocks': all_blocks}

def run_granular_classification(start_dir, debug=False):
    script_path = os.path.abspath(__file__)
    script_dir = os.path.dirname(script_path)
    repo_root = os.path.dirname(script_dir)
    target_dir = os.path.abspath(start_dir)  # Direct absolute resolution for simplicity
    if not os.path.isdir(target_dir):
        print(f"Error: Target directory not found at: {target_dir}")
        return

    automatable_files_by_lang = defaultdict(set)
    manual_files = defaultdict(list)
    total_files_scanned = 0
    total_files_with_callouts = 0

    print(f"Starting granular classification scan in: {target_dir}")
    print("-----------------------------------------------------------------")
    
    for root, _, files in os.walk(target_dir):
        for file_name in files:
            if file_name.lower().endswith(('.adoc', '.asciidoc')):
                file_path = os.path.join(root, file_name)
                total_files_scanned += 1
                
                file_results = process_file(file_path, debug)
                
                if file_results['status'] == 'no_callout_block':
                    continue

                total_files_with_callouts += 1
                
                file_is_clean = True
                has_callout_blocks = False
                for block in file_results['blocks']:
                    status = block['status']
                    if status == 'automatable':
                        has_callout_blocks = True
                    elif status == 'plain_source_block':
                        continue  # Tolerate plain blocks
                    else:
                        manual_files[status].append((file_path, block['reason']))
                        file_is_clean = False
                        break  # Early exit on first manual block
                
                # Only aggregate if clean *and* has at least one callout block
                if file_is_clean and has_callout_blocks:
                    for block in file_results['blocks']:
                        if block['status'] == 'automatable':
                            lang_key = f'automatable_{block["language"]}'
                            automatable_files_by_lang[lang_key].add(file_path)
    
    total_automatable = sum(len(files) for files in automatable_files_by_lang.values())
    total_manual = sum(len(files) for files in manual_files.values())
    
    print("\n\n--- Granular Classification Summary ---")
    print(f"Total Files Scanned: {total_files_scanned}")
    print(f"Total Files with Source Blocks: {total_files_with_callouts}")
    print(f"----------------------------------------")
    print(f"Total Files Ready for Automation: {total_automatable}")
    print(f"Total Files Needing Manual Review: {total_manual}")
    print("----------------------------------------\n")
    
    print("--- Files Ready for Automated Conversion (Grouped by Language) ---")
    for key, files in sorted(automatable_files_by_lang.items()):
        lang_display = key.replace('automatable_', '').upper()
        print(f" {lang_display} ({len(files)} files)")

    print("\n--- Files Requiring Manual Intervention ---")
    for key, files in sorted(manual_files.items()):
        print(f" {key.replace('manual_', '').upper()} ({len(files)} files)")
        
    automatable_output = {lang: sorted(list(files)) for lang, files in automatable_files_by_lang.items()}
    manual_output = {status: [f[0] for f in sorted(files)] for status, files in manual_files.items()}  # Simplified to paths only
    with open('automatable_lists.json', 'w') as f:
        json.dump(automatable_output, f, indent=2)
    with open('manual_lists.json', 'w') as f:
        json.dump(manual_output, f, indent=2)

    print("\n[SUCCESS] Detailed lists saved to 'automatable_lists.json' and 'manual_lists.json'.")

if __name__ == "__main__":
    debug = False
    try:
        opts, args = getopt.getopt(sys.argv[1:], "d", ["debug"])
        for opt, arg in opts:
            if opt in ('-d', '--debug'):
                debug = True
        start_dir = args[0] if args else '.'  # Default to current dir if no arg
    except getopt.GetoptError as err:
        print(f"Usage: python granular_callout_classifier.py [-d|--debug] <path/to/modules/directory>")
        sys.exit(2)
    except IndexError:
        print("Usage: python granular_callout_classifier.py [-d|--debug] <path/to/modules/directory>")
        sys.exit(1)
    
    run_granular_classification(start_dir, debug)