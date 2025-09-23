#!/usr/bin/env python3
"""
Script to convert YAML callouts to bulleted lists or definition lists
according to the SSG guidelines in update.adoc

This script:
1. Identifies YAML code blocks with callouts (<1>, <2>, etc.)
2. Determines whether to use bulleted lists (for structure) or definition lists (for parameters)
3. Converts the callouts and explanations accordingly
"""

import re
import os
import sys
from pathlib import Path
from typing import List, Tuple, Dict, Optional
import argparse


class YAMLCalloutConverter:
    def __init__(self, dry_run: bool = False):
        self.dry_run = dry_run
        self.yaml_keywords_structure = {
            'apiVersion', 'kind', 'metadata', 'spec', 'workspaces', 'tasks', 'params', 
            'runAfter', 'taskRef', 'resolver', 'sources', 'policy', 'data', 'config',
            'include', 'exclude', 'name', 'value', 'workspace'
        }
        self.parameter_indicators = {
            'placeholder', 'user-replaced', 'specify', 'enter', 'replace', 'variable',
            'parameter', 'option', 'field', 'setting'
        }

    def find_yaml_blocks_with_callouts(self, content: str) -> List[Dict]:
        """Find ONLY YAML code blocks that contain callouts"""
        blocks = []
        
        # Pattern to match ONLY YAML code blocks with callouts
        # Must explicitly have 'yaml' in the source block definition
        yaml_block_pattern = r'\[source,\s*yaml[^\]]*\]\s*\n----\s*\n(.*?)\n----'
        callout_explanation_pattern = r'^<(\d+)>\s+(.+?)(?=^<\d+>|^$|\Z)'
        
        yaml_matches = re.finditer(yaml_block_pattern, content, re.DOTALL | re.MULTILINE)
        
        for match in yaml_matches:
            yaml_content = match.group(1)
            block_start = match.start()
            block_end = match.end()
            
            # Check if this YAML block has callouts
            callouts_in_yaml = re.findall(r'<(\d+)>', yaml_content)
            if not callouts_in_yaml:
                continue
            
            # Additional validation: ensure this actually looks like YAML
            # YAML should have key-value pairs with colons, not shell commands
            if not self._is_valid_yaml_content(yaml_content):
                continue
            
            # Find the explanations after the code block
            remaining_content = content[block_end:]
            explanations = {}
            
            # Look for callout explanations - improved pattern
            for callout_num in callouts_in_yaml:
                pattern = rf'^<{callout_num}>\s+(.+?)(?=\n<\d+>|\n\n|\n\.|$)'
                match = re.search(pattern, remaining_content, re.MULTILINE | re.DOTALL)
                if match:
                    explanation = match.group(1).strip()
                    explanations[callout_num] = explanation
            
            if explanations:
                blocks.append({
                    'yaml_content': yaml_content,
                    'callouts': callouts_in_yaml,
                    'explanations': explanations,
                    'start_pos': block_start,
                    'end_pos': block_end,
                    'full_match': match.group(0)
                })
        
        return blocks

    def _is_valid_yaml_content(self, content: str) -> bool:
        """
        Validate that content actually looks like YAML, not shell commands or other code
        """
        lines = content.strip().split('\n')
        if not lines:
            return False
        
        yaml_indicators = 0
        non_yaml_indicators = 0
        
        for line in lines:
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            
            # YAML indicators
            if ':' in line and not line.startswith('echo') and not line.startswith('cat'):
                yaml_indicators += 1
            if line.startswith('- ') or line.startswith('  - '):
                yaml_indicators += 1
            if any(keyword in line for keyword in ['apiVersion:', 'kind:', 'metadata:', 'spec:']):
                yaml_indicators += 2
            
            # Non-YAML indicators (shell commands, etc.)
            if any(line.startswith(cmd) for cmd in ['echo', 'cat', 'oc ', 'kubectl', 'podman', 'docker']):
                non_yaml_indicators += 2
            if line.startswith('$') or '$(pwd)' in line or line.endswith(' \\'):
                non_yaml_indicators += 1
            if line.startswith('EOF') or '<<EOF' in line:
                non_yaml_indicators += 2
        
        # Must have more YAML indicators than non-YAML indicators
        return yaml_indicators > non_yaml_indicators and yaml_indicators > 0

    def _clean_explanation_text(self, explanation: str) -> str:
        """
        Clean up explanation text to make it flow better in definition lists
    
        """
        # Pattern to match "Specifies replace `something` with ..." (single backticks)
        pattern = r'^(Specifies\s+)replace\s+`[^`]+`\s+with\s+(.*)'
        match = re.match(pattern, explanation, re.IGNORECASE)
        
        if match:
            prefix = match.group(1)  # "Specifies "
            rest = match.group(2)    # "a unique identifier..."
            return f"{prefix}{rest}"
        
        # Pattern to match "Specifies replace ``something`` with ..." (double backticks)
        pattern2 = r'^(Specifies\s+)replace\s+``[^`]+``\s+with\s+(.*)'
        match2 = re.match(pattern2, explanation, re.IGNORECASE)
        
        if match2:
            prefix = match2.group(1)  # "Specifies "
            rest = match2.group(2)    # "a unique identifier..."
            return f"{prefix}{rest}"
        
        # Pattern to match "Replace `something` with ..." (single backticks)
        replace_pattern = r'^Replace\s+`[^`]+`\s+with\s+(.*)'
        replace_match = re.match(replace_pattern, explanation, re.IGNORECASE)
        
        if replace_match:
            rest = replace_match.group(1)
            # Capitalize first letter if it's not already
            if rest and rest[0].islower():
                rest = rest[0].upper() + rest[1:]
            return f"Specifies {rest.lower()}"
        
        # Pattern to match "Replace ``something`` with ..." (double backticks)
        replace_pattern2 = r'^Replace\s+``[^`]+``\s+with\s+(.*)'
        replace_match2 = re.match(replace_pattern2, explanation, re.IGNORECASE)
        
        if replace_match2:
            rest = replace_match2.group(1)
            # Capitalize first letter if it's not already
            if rest and rest[0].islower():
                rest = rest[0].upper() + rest[1:]
            return f"Specifies {rest.lower()}"
        
        return explanation

    def determine_conversion_type(self, yaml_content: str, explanations: Dict[str, str]) -> str:
        """
        Determine whether to use bulleted lists (structure) or definition lists (parameters)
        
        Returns: 'bulleted' for structure explanations, 'definition' for parameter explanations
        """
        structure_score = 0
        parameter_score = 0
        
        # Analyze YAML content for structure keywords (but don't over-weight basic YAML structure)
        yaml_lines = yaml_content.split('\n')
        for line in yaml_lines:
            line_clean = line.strip().lower()
            # Only count structural keywords that are typically explained in structure docs
            structural_keywords = {'workspaces', 'tasks', 'params', 'sources', 'policy', 'data', 'config'}
            for keyword in structural_keywords:
                if keyword + ':' in line_clean:
                    structure_score += 1
        
        # Check if YAML content has user-replaced values (angle brackets)
        if re.search(r'<[^>]+>', yaml_content):
            parameter_score += 3
        
        # Analyze explanations for parameter indicators
        for explanation in explanations.values():
            explanation_lower = explanation.lower()
            
            # Check for parameter-like language
            for indicator in self.parameter_indicators:
                if indicator in explanation_lower:
                    parameter_score += 1
            
            # Check for angle brackets (user-replaced values)
            if '<' in explanation and '>' in explanation:
                parameter_score += 3
            
            # Check for "Specifies" language (definition list indicator)
            if explanation_lower.startswith('specifies') or 'specifies the' in explanation_lower:
                parameter_score += 2
            
            # Check for structure-like language
            if any(word in explanation_lower for word in ['list of', 'definition of', 'structure', 'contains']):
                structure_score += 1
        
        # Decision logic
        if parameter_score > structure_score:
            return 'definition'
        else:
            return 'bulleted'

    def convert_to_bulleted_list(self, yaml_content: str, callouts: List[str], explanations: Dict[str, str]) -> str:
        """Convert callouts to bulleted list format"""
        # Remove callouts from YAML while preserving indentation
        clean_yaml = yaml_content
        for callout in callouts:
            # Remove callout and any trailing comment characters (# etc.) but preserve indentation
            clean_yaml = re.sub(rf'<{callout}>(\s*#.*)?$', '', clean_yaml, flags=re.MULTILINE)
        
        # Create bulleted list
        bulleted_explanations = []
        for callout in sorted(callouts, key=int):
            if callout in explanations:
                explanation = explanations[callout]
                # Extract the key being explained (try to find it in the explanation or YAML)
                key_match = re.search(r'`([^`]+)`', explanation)
                if key_match:
                    key = key_match.group(1)
                    bulleted_explanations.append(f"* `{key}`: {explanation}")
                else:
                    # Try to infer from YAML content
                    yaml_lines = yaml_content.split('\n')
                    callout_line = None
                    for line in yaml_lines:
                        if f'<{callout}>' in line:
                            callout_line = line.strip()
                            break
                    
                    if callout_line:
                        # Extract key from YAML line
                        key_match = re.match(r'([^:]+):', callout_line.replace(f'<{callout}>', '').strip())
                        if key_match:
                            key = key_match.group(1).strip()
                            bulleted_explanations.append(f"* `{key}`: {explanation}")
                        else:
                            bulleted_explanations.append(f"* {explanation}")
                    else:
                        bulleted_explanations.append(f"* {explanation}")
        
        return clean_yaml, '\n'.join(bulleted_explanations)

    def convert_to_definition_list(self, yaml_content: str, callouts: List[str], explanations: Dict[str, str]) -> str:
        """Convert callouts to definition list format"""
        # Remove callouts from YAML while preserving indentation
        clean_yaml = yaml_content
        for callout in callouts:
            # Remove callout and any trailing comment characters (# etc.) but preserve indentation
            # This handles cases like: value: <segment_key> # <3>
            clean_yaml = re.sub(rf'<{callout}>(\s*#.*)?$', '', clean_yaml, flags=re.MULTILINE)
            # Also clean up any remaining standalone # at end of lines
            clean_yaml = re.sub(r'\s+#\s*$', '', clean_yaml, flags=re.MULTILINE)
        
        # Create definition list with "where:" introduction
        definition_explanations = ["where:", ""]
        
        for callout in sorted(callouts, key=int):
            if callout in explanations:
                explanation = explanations[callout]
                
                # Extract placeholder/parameter name
                placeholder_match = re.search(r'<([^>]+)>', explanation)
                if placeholder_match:
                    placeholder = placeholder_match.group(1)
                    # Format as definition list item and clean up the text
                    clean_explanation = explanation.replace(f'<{placeholder}>', f'`{placeholder}`')
                    clean_explanation = self._clean_explanation_text(clean_explanation)
                    if not clean_explanation.lower().startswith('specifies'):
                        clean_explanation = f"Specifies {clean_explanation.lower()}"
                    definition_explanations.append(f"<{placeholder}>:: {clean_explanation}")
                else:
                    # Try to extract from YAML content or create generic entry
                    yaml_lines = yaml_content.split('\n')
                    callout_line = None
                    for line in yaml_lines:
                        if f'<{callout}>' in line:
                            callout_line = line.strip()
                            break
                    
                    if callout_line and ':' in callout_line:
                        key_match = re.match(r'([^:]+):', callout_line.replace(f'<{callout}>', '').strip())
                        if key_match:
                            key = key_match.group(1).strip()
                            clean_explanation = self._clean_explanation_text(explanation)
                            if not clean_explanation.lower().startswith('specifies'):
                                clean_explanation = f"Specifies {clean_explanation.lower()}"
                            definition_explanations.append(f"<{key}>:: {clean_explanation}")
                        else:
                            clean_explanation = explanation
                            if not clean_explanation.lower().startswith('specifies'):
                                clean_explanation = f"Specifies {clean_explanation.lower()}"
                            definition_explanations.append(f"<parameter-{callout}>:: {clean_explanation}")
                    else:
                        clean_explanation = self._clean_explanation_text(explanation)
                        if not clean_explanation.lower().startswith('specifies'):
                            clean_explanation = f"Specifies {clean_explanation.lower()}"
                        definition_explanations.append(f"<parameter-{callout}>:: {clean_explanation}")
        
        return clean_yaml, '\n'.join(definition_explanations)

    def process_file(self, file_path: str) -> bool:
        """Process a single AsciiDoc file"""
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                content = f.read()
            
            blocks = self.find_yaml_blocks_with_callouts(content)
            if not blocks:
                return False
            
            print(f"\n Processing: {file_path}")
            print(f"   Found {len(blocks)} YAML blocks with callouts")
            
            modified_content = content
            offset = 0  # Track position changes due to replacements
            
            for i, block in enumerate(blocks):
                conversion_type = self.determine_conversion_type(block['yaml_content'], block['explanations'])
                print(f"   Block {i+1}: Converting to {conversion_type} list")
                
                if conversion_type == 'bulleted':
                    clean_yaml, new_explanations = self.convert_to_bulleted_list(
                        block['yaml_content'], block['callouts'], block['explanations']
                    )
                else:
                    clean_yaml, new_explanations = self.convert_to_definition_list(
                        block['yaml_content'], block['callouts'], block['explanations']
                    )
                
                # Create the new code block with proper AsciiDoc continuation
                new_yaml_block = f"[source,yaml]\n----\n{clean_yaml}\n----\n+\n{new_explanations}"
                
                # Find and replace the original block and its explanations
                original_block_start = block['start_pos'] + offset
                
                # Find where explanations end
                remaining_content = modified_content[original_block_start + len(block['full_match']):]
                explanations_end = 0
                
                # Find the end of all callout explanations
                for callout in block['callouts']:
                    pattern = rf'^<{callout}>\s+.+?(?=^<\d+>|^$|\Z|\n\n)'
                    match = re.search(pattern, remaining_content, re.MULTILINE | re.DOTALL)
                    if match:
                        explanations_end = max(explanations_end, match.end())
                
                original_block_end = original_block_start + len(block['full_match']) + explanations_end
                
                # Replace the content
                modified_content = (
                    modified_content[:original_block_start] + 
                    new_yaml_block + 
                    modified_content[original_block_end:]
                )
                
                # Update offset for next replacements
                offset += len(new_yaml_block) - (original_block_end - original_block_start)
            
            if self.dry_run:
                print(f"   [DRY RUN] Would update file")
                return True
            else:
                with open(file_path, 'w', encoding='utf-8') as f:
                    f.write(modified_content)
                print(f"   Updated file")
                return True
                
        except Exception as e:
            print(f"   Error processing {file_path}: {e}")
            return False

    def process_directory(self, directory: str) -> None:
        """Process all .adoc files in a directory"""
        processed_files = 0
        updated_files = 0
        
        for root, dirs, files in os.walk(directory):
            for file in files:
                if file.endswith('.adoc'):
                    file_path = os.path.join(root, file)
                    processed_files += 1
                    if self.process_file(file_path):
                        updated_files += 1
        
        print(f"\n Summary:")
        print(f"   Processed: {processed_files} files")
        print(f"   Updated: {updated_files} files")
        if self.dry_run:
            print(f"   (Dry run mode - no files were actually modified)")


def main():
    parser = argparse.ArgumentParser(
        description="Convert YAML callouts to bulleted or definition lists based on SSG guidelines"
    )
    parser.add_argument(
        'path',
        help='Path to .adoc file or directory to process'
    )
    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='Show what would be changed without modifying files'
    )
    
    args = parser.parse_args()
    
    converter = YAMLCalloutConverter(dry_run=args.dry_run)
    
    if os.path.isfile(args.path):
        if args.path.endswith('.adoc'):
            converter.process_file(args.path)
        else:
            print("Error: File must have .adoc extension")
            sys.exit(1)
    elif os.path.isdir(args.path):
        converter.process_directory(args.path)
    else:
        print(f"Error: Path '{args.path}' does not exist")
        sys.exit(1)


if __name__ == "__main__":
    main()
 