"""
Shared utilities for all callout converters.

This module contains common functions used across all language-specific converters
to ensure consistency and maintainability (DRY principle).
"""

import re


def parse_and_replace_definitions(def_content, terms, use_backticks=True, debug=False):
    """
    Parse callout definitions and convert to definition list format.
    
    This is the core conversion logic shared across all language converters.
    
    Args:
        def_content (str): The raw definition block content after the code block
        terms (dict): Mapping of marker numbers to extracted term names
        use_backticks (bool): Whether to wrap terms in backticks (True for code terms, False for config keys)
        debug (bool): Enable debug output
    
    Returns:
        tuple: (formatted_definitions, is_complete)
            - formatted_definitions: The converted definition list as a string
            - is_complete: True if all markers were successfully converted, False otherwise
    
    Features:
        - Handles multi-line explanations
        - Preserves nested lists within definitions
        - Handles AsciiDoc continuation (+)
        - Validates completeness (no lingering markers)
    """
    def_lines = def_content.splitlines()
    new_defs = []
    marker_pattern = re.compile(r'^\s*<(\d+)>\s*(.*)$')
    continuation_pattern = re.compile(r'^\s*\+$')
    list_item_pattern = re.compile(r'^\s*[*+-]\s+')
    current_explanation = []
    current_marker = None

    for line in def_lines + ['']:  # Trailing '' flushes final state
        stripped_line = line.strip()
        is_continuation = continuation_pattern.match(line)
        is_list_item = list_item_pattern.match(line)

        match = marker_pattern.match(line)
        if match:
            # Flush prior explanation
            if current_marker:
                joined_exp = '\n'.join(current_explanation).strip()
                if current_marker in terms:
                    # Format term with or without backticks based on language type
                    term_formatted = f"`{terms[current_marker]}`" if use_backticks else terms[current_marker]
                    new_defs.append(f"{term_formatted}:: {joined_exp}")
                else:
                    # Fallback for unmatched markers
                    new_defs.append(line)
            
            current_marker = int(match.group(1))
            current_explanation = [match.group(2)]
            
            if debug:
                print(f"Debug: New marker {current_marker}, initial exp: '{match.group(2)}'")
            continue

        if is_continuation:
            # Hard break: Add \n to prior text (if exists and non-list), skip +
            if current_explanation and current_explanation[-1].strip() and not list_item_pattern.match(current_explanation[-1]):
                current_explanation[-1] += '\n'
            # Silent skip preserves transition to lists
            continue
        elif is_list_item:
            # Append verbatim to maintain indentation and hierarchy
            current_explanation.append(line)
            if debug:
                print(f"Debug: Preserved list item: '{line.strip()}'")
            continue
        elif stripped_line:
            # Standard append for text/blanks
            current_explanation.append(line)
        else:
            # Preserve blank lines as separators
            current_explanation.append(line)

    # Final flush
    if current_marker:
        joined_exp = '\n'.join(current_explanation).strip()
        if current_marker in terms:
            term_formatted = f"`{terms[current_marker]}`" if use_backticks else terms[current_marker]
            new_defs.append(f"{term_formatted}:: {joined_exp}")
        else:
            new_defs.append('\n'.join(current_explanation))

    # Sort by marker number (inf fallback for non-matches)
    def sort_key(l):
        match = re.search(r'<(\d+)>', l)
        return int(match.group(1)) if match else float('inf')
    sorted_defs = sorted(new_defs, key=sort_key)

    # Completeness check: No lingering markers
    complete = len(re.findall(r'<(\d+)>', '\n'.join(sorted_defs))) == 0
    
    return '\n\n'.join(sorted_defs), complete


def detect_edge_cases(source_lines, marker_pattern=None):
    """
    Detect edge cases in source code that may require manual review.
    
    Args:
        source_lines (list): Lines of source code
        marker_pattern (re.Pattern, optional): Compiled regex for markers. Defaults to <N> pattern.
    
    Returns:
        tuple: (has_issues, issue_type, issue_description)
    
    Edge cases detected:
        - Comment-only callouts
        - All-caps placeholders (USER, PASSWORD, etc.) suggesting semantic changes needed
        - Multiple markers on same line
    """
    if marker_pattern is None:
        marker_pattern = re.compile(r'<(\d+)>')
    
    for line_num, line in enumerate(source_lines, start=1):
        markers = marker_pattern.findall(line)
        if not markers:
            continue
        
        # Check for multiple markers on same line
        if len(markers) > 1:
            return (True, 'multiple_markers', f'Line {line_num} has {len(markers)} markers')
        
        # Extract pre-marker content
        marker_pos = line.find('<')
        if marker_pos == -1:
            continue
        pre_marker = line[:marker_pos].strip()
        
        # Edge case: Comment-only line (just # or // with no other content)
        if re.match(r'^[#/]+\s*$', pre_marker):
            return (True, 'comment_only_callout', f'Line {line_num} has callout on comment-only line')
        
        # Edge case: All-caps tokens suggesting semantic placeholders
        # Example: USER, PASSWORD, URL - these usually need refactoring, not just term extraction
        all_caps_tokens = re.findall(r'\b[A-Z_]{3,}\b', pre_marker)
        if all_caps_tokens and len(all_caps_tokens) >= 2:
            return (True, 'semantic_placeholders', 
                   f'Line {line_num} has all-caps tokens ({", ".join(all_caps_tokens[:3])}) suggesting semantic refactoring needed')
    
    return (False, None, None)


def clean_source_line(line, language_type='generic'):
    """
    Clean a source code line by removing callout markers and trailing comments.
    
    Args:
        line (str): The source line to clean
        language_type (str): Language type for language-specific cleaning
            Options: 'yaml', 'json', 'shell', 'python', 'go', 'generic'
    
    Returns:
        str: Cleaned line
    """
    # First, remove the marker itself
    cleaned = re.sub(r'<(\d+)>', '', line).rstrip()
    
    # Language-specific comment handling
    comment_patterns = {
        'yaml': r'#',
        'python': r'#',
        'shell': r'#',
        'go': r'//',
        'json': r'(?://|#)',  # JSON doesn't have comments, but handle anyway
        'generic': r'#'
    }
    
    comment_char = comment_patterns.get(language_type, '#')
    
    # Remove trailing comment if it was only for the callout
    if re.search(rf'{comment_char}\s*<\d+>\s*$', line):
        cleaned = re.sub(rf'{comment_char}\s*<\d+>\s*$', '', cleaned).rstrip()
    
    # Also clean standalone trailing comment markers
    cleaned = re.sub(rf'\s+{comment_char}\s*$', '', cleaned)
    
    return cleaned


def validate_marker_sequence(markers):
    """
    Validate that markers are sequential (1, 2, 3...) not (1, 3, 5...).
    
    Args:
        markers (list or set): Collection of marker numbers
    
    Returns:
        tuple: (is_valid, expected_sequence, actual_sequence)
    """
    if not markers:
        return (True, [], [])
    
    sorted_markers = sorted([int(m) if isinstance(m, str) else m for m in markers])
    expected_sequence = list(range(1, len(sorted_markers) + 1))
    
    is_valid = sorted_markers == expected_sequence
    
    return (is_valid, expected_sequence, sorted_markers)


def get_block_pattern():
    """
    Get the standard regex pattern for matching AsciiDoc code blocks with callouts.
    
    This pattern is used by BOTH the classifier and all converters to ensure consistency.
    
    Matches the following block types:
    1. [source,yaml,subs=...] - standard with language
    2. [source,terminal,subs=...] - terminal language  
    3. [source,subs=...] - no language, has subs (defaults to 'shell')
    4. [source] - no language, no subs (defaults to 'shell')
    5. [subs=...] - no source prefix (defaults to 'shell')
    
    Does NOT match:
    - [id=...], [role=...] or other non-source/subs blocks
    
    Returns:
        re.Pattern: Compiled regex pattern
    """
    pattern_string = (
        # Group 1: Full header line
        # Matches: [source,lang,...] OR [source,subs=...] OR [source] OR [subs=...]
        r'(\s*\[(?:source(?:,([a-zA-Z0-9_\-]*))?|subs=)[^\]]*\]\s*\n)'
        # Group 3: Opening delimiter (----)
        r'(\s*-{4,}\s*\n)'
        # Group 4: Source content
        r'(.*?)\n'
        # Closing delimiter
        r'\s*-{4,}\s*\n'
        # Group 5: Definition content (callout explanations)
        r'(.*?)'
        # Lookahead for end of block
        # Note: \n\.{1,5}\s matches AsciiDoc list markers (.., ..., etc.) followed by space
        # Note: \n\.\w matches single dot followed by word char (e.g., .Procedure)
        r'(?=\n\.{2,5}\s|\n\.\w|\n\[source|\n\[subs|\n=|--|\Z)'
    )
    return re.compile(pattern_string, re.MULTILINE | re.DOTALL)


def normalize_language(lang):
    """
    Normalize the language identifier, defaulting to 'shell' when empty or unspecified.
    
    Args:
        lang (str): The captured language from the source block header
        
    Returns:
        str: Normalized language identifier
    """
    if not lang or lang.startswith('subs'):
        return 'shell'
    return lang.lower()


# Error messages for better user feedback
ERROR_MESSAGES = {
    'comment_only_callout': 'Callout appears on a comment-only line with no code term to extract',
    'semantic_placeholders': 'Line contains all-caps placeholders (e.g., USER, PASSWORD) suggesting semantic refactoring is needed',
    'multiple_markers': 'Multiple callout markers on the same line',
    'non_sequential': 'Callout markers are not sequential (expected 1, 2, 3... sequence)',
    'duplicate_marker': 'The same marker number appears multiple times',
    'empty_term': 'Cannot extract a meaningful term from the line before the marker',
    'duplicate_extracted_term': 'The same term was extracted multiple times, which would create an invalid definition list',
}


def get_error_message(error_type, context=''):
    """
    Get a user-friendly error message for a given error type.
    
    Args:
        error_type (str): The type of error
        context (str, optional): Additional context to append
    
    Returns:
        str: Formatted error message
    """
    base_message = ERROR_MESSAGES.get(error_type, f'Unknown error: {error_type}')
    if context:
        return f"{base_message}. {context}"
    return base_message


def validate_unique_terms(terms):
    """
    Validate that all extracted terms are unique.
    
    Raises ValueError if duplicate terms are found, which prevents creating
    invalid definition lists with duplicate term names.
    
    Args:
        terms (dict): Dictionary mapping marker numbers to term strings
        
    Raises:
        ValueError: If duplicate terms are detected
    """
    if not terms:
        return
    
    # Get all term values (excluding None and empty strings)
    term_values = [v for v in terms.values() if v]
    
    # Check for duplicates
    seen = set()
    duplicates = set()
    for term in term_values:
        if term in seen:
            duplicates.add(term)
        seen.add(term)
    
    if duplicates:
        dup_list = ', '.join(f'"{d}"' for d in sorted(duplicates))
        raise ValueError(get_error_message('duplicate_extracted_term', 
                                          f'Duplicate terms found: {dup_list}'))

