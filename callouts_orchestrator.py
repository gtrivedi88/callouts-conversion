#!/usr/bin/env python3
"""
Unified Callouts Conversion Orchestrator

This script automatically:
1. Scans AsciiDoc files for code blocks with callouts
2. Classifies them as automatable vs. manual review needed
3. Routes to appropriate converters (YAML, JSON, bash, etc.)
4. Generates comprehensive reports

Modes:
- Default: Scan all .adoc files in target directory (follows symlinks)
- Assembly mode (--assembly-mode): Only convert modules referenced by assemblies
  in the target directory. This is useful when you want to convert only the
  modules that are actually used by a specific set of assemblies.
"""

import sys
import os
import re
import argparse
from pathlib import Path
from collections import defaultdict
import json
from datetime import datetime

# Import our modules
try:
    from granular_callput import analyze_block, SUPPORTED_LANGUAGES
    from converter_utils import get_block_pattern, clean_source_line
    from yaml_callout import process_file as yaml_process_file
    from json_callout import process_file as json_process_file
    from shell_callout import process_file as shell_process_file
    from python_callout import process_file as python_process_file
    from go_callout import process_file as go_process_file
    from generic_callout import process_file as generic_process_file
except ImportError as e:
    print(f"Error: Required modules not found. Ensure all converter modules are in the same directory.")
    print(f"Details: {e}")
    sys.exit(1)


class CalloutsOrchestrator:
    def __init__(self, target_path, dry_run=False, debug=False, assembly_mode=False):
        self.target_path = Path(target_path).resolve()
        self.dry_run = dry_run
        self.debug = debug
        self.assembly_mode = assembly_mode
        self.single_file_mode = self.target_path.is_file()
        
        # For directory mode, keep target_dir for compatibility
        self.target_dir = self.target_path if not self.single_file_mode else self.target_path.parent
        
        # For assembly mode: track which files to process
        self.files_to_process = set()
        
        # Statistics
        self.stats = {
            'total_files_scanned': 0,
            'files_with_source_blocks': 0,
            'files_converted': defaultdict(int),
            'files_manual_review': defaultdict(list),
            'files_skipped': defaultdict(list),
            'files_with_errors': [],
            'blocks_converted': defaultdict(int),
            'assemblies_found': 0,
            'includes_resolved': 0
        }
        
        # Classification results
        self.automatable_by_lang = defaultdict(set)
        self.manual_review_files = defaultdict(list)
        
    def validate_environment(self):
        """Validate the target path exists and is accessible"""
        if not self.target_path.exists():
            print(f"❌ Error: Target path does not exist: {self.target_path}")
            return False
        
        if self.single_file_mode:
            # Single file validation
            if not self.target_path.suffix.lower() in ('.adoc', '.asciidoc'):
                print(f"❌ Error: File is not an AsciiDoc file: {self.target_path}")
                return False
            
            if not os.access(self.target_path, os.R_OK):
                print(f"❌ Error: No read permission for file: {self.target_path}")
                return False
            
            if self.assembly_mode:
                print(f"⚠️  Warning: --assembly-mode is ignored for single file processing")
                self.assembly_mode = False
        else:
            # Directory validation
            if not self.target_path.is_dir():
                print(f"❌ Error: Target path is not a directory: {self.target_path}")
                return False
            
            if not os.access(self.target_path, os.R_OK):
                print(f"❌ Error: No read permission for directory: {self.target_path}")
                return False
        
        return True
    
    def extract_includes_from_file(self, file_path):
        """
        Extract all include:: directives from an AsciiDoc file.
        Returns a list of resolved file paths.
        """
        includes = []
        include_pattern = re.compile(r'^include::([^\[]+)\[', re.MULTILINE)
        
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                content = f.read()
        except Exception as e:
            if self.debug:
                print(f"  DEBUG: Error reading {file_path}: {e}")
            return includes
        
        for match in include_pattern.finditer(content):
            include_path = match.group(1).strip()
            
            # Skip attribute references like {snippets-dir}/...
            if '{' in include_path:
                if self.debug:
                    print(f"  DEBUG: Skipping attribute-based include: {include_path}")
                continue
            
            # Resolve the path relative to the file's directory
            file_dir = Path(file_path).parent
            resolved_path = (file_dir / include_path).resolve()
            
            # Check if file exists (following symlinks)
            if resolved_path.exists():
                includes.append(resolved_path)
                if self.debug:
                    print(f"  DEBUG: Resolved include: {include_path} -> {resolved_path}")
            else:
                if self.debug:
                    print(f"  DEBUG: Include not found: {include_path} (tried {resolved_path})")
        
        return includes
    
    def is_assembly_file(self, file_path):
        """
        Check if a file is an assembly file (contains :_mod-docs-content-type: ASSEMBLY).
        """
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                # Read first 20 lines to check for assembly marker
                for i, line in enumerate(f):
                    if i > 20:
                        break
                    if ':_mod-docs-content-type: ASSEMBLY' in line:
                        return True
        except Exception:
            pass
        return False
    
    def collect_assembly_includes(self):
        """
        In assembly mode, find all assemblies in target directory and collect
        their included module files (recursively).
        """
        print(f"📂 Assembly Mode: Scanning for assemblies in {self.target_dir}")
        
        visited = set()
        
        def collect_recursive(file_path, depth=0):
            """Recursively collect includes from a file."""
            real_path = Path(file_path).resolve()
            
            if real_path in visited:
                return
            visited.add(real_path)
            
            # Add this file to the processing list
            self.files_to_process.add(real_path)
            
            # Get includes from this file
            includes = self.extract_includes_from_file(real_path)
            self.stats['includes_resolved'] += len(includes)
            
            # Recursively process includes
            for include_path in includes:
                if include_path.suffix.lower() in ('.adoc', '.asciidoc'):
                    collect_recursive(include_path, depth + 1)
        
        # Find all assembly files in the target directory (NOT following symlinks for assemblies)
        for root, dirs, files in os.walk(self.target_dir):
            # Skip hidden directories
            dirs[:] = [d for d in dirs if not d.startswith('.')]
            
            # Don't follow symlinks when looking for assemblies
            # We only want assemblies that are physically in the target directory
            real_root = os.path.realpath(root)
            if real_root != root and not root.startswith(str(self.target_dir)):
                continue
            
            for filename in files:
                if not filename.lower().endswith(('.adoc', '.asciidoc')):
                    continue
                
                file_path = Path(root) / filename
                
                # Check if it's an assembly
                if self.is_assembly_file(file_path):
                    self.stats['assemblies_found'] += 1
                    if self.debug:
                        print(f"  📄 Found assembly: {file_path}")
                    
                    # Collect all includes from this assembly
                    collect_recursive(file_path)
        
        print(f"   Found {self.stats['assemblies_found']} assemblies")
        print(f"   Resolved {self.stats['includes_resolved']} includes")
        print(f"   Total files to process: {len(self.files_to_process)}")
        print("=" * 70)
    
    def is_valid_adoc_file(self, file_path):
        """
        Validate that a file is actually an AsciiDoc file and not binary
        Edge cases: symlinks, binary files with .adoc extension, empty files
        """
        try:
            # Check if it's a symlink
            if file_path.is_symlink():
                if self.debug:
                    print(f"  DEBUG: Skipping symlink: {file_path}")
                self.stats['files_skipped']['symlinks'].append(str(file_path))
                return False
            
            # Check file size (skip empty or suspiciously large files)
            file_size = file_path.stat().st_size
            if file_size == 0:
                if self.debug:
                    print(f"  DEBUG: Skipping empty file: {file_path}")
                self.stats['files_skipped']['empty'].append(str(file_path))
                return False
            
            if file_size > 50 * 1024 * 1024:  # 50MB
                if self.debug:
                    print(f"  DEBUG: Skipping large file (>50MB): {file_path}")
                self.stats['files_skipped']['too_large'].append(str(file_path))
                return False
            
            # Try to read first few bytes to detect binary
            with open(file_path, 'rb') as f:
                chunk = f.read(1024)
                if b'\x00' in chunk:  # Null bytes indicate binary
                    if self.debug:
                        print(f"  DEBUG: Skipping binary file: {file_path}")
                    self.stats['files_skipped']['binary'].append(str(file_path))
                    return False
            
            return True
            
        except PermissionError:
            self.stats['files_skipped']['no_permission'].append(str(file_path))
            return False
        except Exception as e:
            if self.debug:
                print(f"  DEBUG: Error validating {file_path}: {e}")
            self.stats['files_skipped']['validation_error'].append(str(file_path))
            return False
    
    def classify_file(self, file_path):
        """
        Classify a single file's callout blocks
        Returns: (automatable_langs, manual_issues, error)
        """
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                content = f.read()
        except UnicodeDecodeError:
            # Try other encodings
            try:
                with open(file_path, 'r', encoding='latin-1') as f:
                    content = f.read()
            except Exception as e:
                return None, None, f"Encoding error: {e}"
        except Exception as e:
            return None, None, f"Read error: {e}"
        
        pattern = get_block_pattern()
        automatable_langs = set()
        manual_issues = []
        has_any_blocks = False
        
        for match in pattern.finditer(content):
            language = match.group(2).lower()
            
            # Skip unsupported languages
            if language not in SUPPORTED_LANGUAGES:
                if self.debug:
                    print(f"  DEBUG: Unsupported language '{language}' in {file_path}")
                continue
            
            has_any_blocks = True
            source_content = match.group(4)
            definition_content = match.group(5)
            
            # Check for edge case: already converted (has `::` definition list)
            if '::' in definition_content and '<' not in definition_content:
                if self.debug:
                    print(f"  DEBUG: Already converted definition list in {file_path}")
                manual_issues.append(('already_converted', 'File appears to already have definition lists'))
                continue
            
            # Validate marker sequence (should be sequential: <1>, <2>, <3>...)
            source_markers = [int(m) for m in sorted(set(
                int(m) for m in __import__('re').findall(r'<(\d+)>', source_content)
            ))]
            if source_markers and source_markers != list(range(1, len(source_markers) + 1)):
                manual_issues.append((
                    'non_sequential_markers',
                    f'Markers are not sequential: {source_markers}'
                ))
                continue
            
            # Run the analyzer
            status, reason = analyze_block(source_content, definition_content, self.debug)
            
            if status == 'automatable':
                automatable_langs.add(language)
            elif status != 'plain_source_block':
                manual_issues.append((status, reason))
        
        if not has_any_blocks:
            return None, None, None  # No source blocks at all
        
        return automatable_langs, manual_issues, None
    
    def scan_and_classify(self):
        """Phase 1: Scan all files and classify them"""
        print(f"🔍 Scanning directory: {self.target_dir}")
        print("=" * 70)
        
        # In assembly mode, first collect the files to process
        if self.assembly_mode:
            self.collect_assembly_includes()
            files_to_scan = self.files_to_process
        else:
            # Default mode: scan all files (following symlinks)
            files_to_scan = self._collect_all_files()
        
        # Now classify each file
        for file_path in files_to_scan:
            file_path = Path(file_path)
            self.stats['total_files_scanned'] += 1
            
            # Validate file
            if not self.is_valid_adoc_file(file_path):
                continue
            
            # Classify
            automatable_langs, manual_issues, error = self.classify_file(file_path)
            
            if error:
                self.stats['files_with_errors'].append((str(file_path), error))
                continue
            
            if automatable_langs is None and manual_issues is None:
                # No source blocks
                continue
            
            self.stats['files_with_source_blocks'] += 1
            
            # NEW LOGIC: Add to automatable list even if file has some manual blocks
            # This allows block-level processing to convert what it can
            if automatable_langs:
                for lang in automatable_langs:
                    self.automatable_by_lang[lang].add(str(file_path))
            
            # Also log manual review issues for reporting
            if manual_issues:
                for issue_type, reason in manual_issues:
                    self.manual_review_files[issue_type].append((str(file_path), reason))
        
        # Print classification summary
        self._print_classification_summary()
    
    def _collect_all_files(self):
        """
        Collect all .adoc files in the target directory.
        Follows symlinks but prevents infinite loops.
        """
        files = set()
        visited_dirs = set()
        
        for root, dirs, filenames in os.walk(self.target_dir, followlinks=True):
            # Resolve real path to detect symlink loops
            real_root = os.path.realpath(root)
            if real_root in visited_dirs:
                if self.debug:
                    print(f"  DEBUG: Skipping symlink loop: {root}")
                dirs[:] = []  # Don't descend further
                continue
            visited_dirs.add(real_root)
            
            # Skip hidden directories
            dirs[:] = [d for d in dirs if not d.startswith('.')]
            
            for filename in filenames:
                if filename.lower().endswith(('.adoc', '.asciidoc')):
                    files.add(Path(root) / filename)
        
        return files
    
    def _print_classification_summary(self):
        """Print the classification phase summary"""
        print(f"\n📊 Classification Summary")
        print("=" * 70)
        print(f"Total files scanned: {self.stats['total_files_scanned']}")
        print(f"Files with source blocks: {self.stats['files_with_source_blocks']}")
        
        total_automatable = sum(len(files) for files in self.automatable_by_lang.values())
        total_manual = sum(len(files) for files in self.manual_review_files.values())
        
        print(f"\n✅ Ready for automation: {total_automatable} files")
        for lang, files in sorted(self.automatable_by_lang.items()):
            print(f"   - {lang.upper()}: {len(files)} files")
        
        print(f"\n⚠️  Needs manual review: {total_manual} files")
        for issue_type, files in sorted(self.manual_review_files.items()):
            print(f"   - {issue_type.replace('_', ' ').title()}: {len(files)} files")
        
        if self.stats['files_skipped']:
            total_skipped = sum(len(files) for files in self.stats['files_skipped'].values())
            print(f"\n⏭️  Skipped: {total_skipped} files")
            for reason, files in sorted(self.stats['files_skipped'].items()):
                if files:
                    print(f"   - {reason.replace('_', ' ').title()}: {len(files)} files")
        
        if self.stats['files_with_errors']:
            print(f"\n❌ Errors: {len(self.stats['files_with_errors'])} files")
    
    def convert_file_blocks(self, file_path, debug=False):
        """
        Convert individual blocks in a file (block-level processing).
        
        This method:
        1. Reads the entire file content into memory
        2. Loops through every code block
        3. Classifies and converts each block individually
        4. Replaces converted blocks in memory
        5. Writes the modified content back once at the end
        
        Returns: (success, blocks_converted_count, blocks_skipped_reasons)
        """
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                content = f.read()
        except Exception as e:
            return False, 0, [f"Read error: {e}"]
        
        pattern = get_block_pattern()
        modified_content = content
        blocks_converted = 0
        blocks_skipped = []
        
        # Process each block independently
        for match in pattern.finditer(content):
            language = match.group(2).lower()
            source_content = match.group(4)
            definition_content = match.group(5)
            original_block = match.group(0)
            
            # Skip unsupported languages
            if language not in SUPPORTED_LANGUAGES:
                blocks_skipped.append(f"Unsupported language: {language}")
                continue
            
            # Check if already converted
            if '::' in definition_content and '<' not in definition_content:
                if debug:
                    print(f"     ⏭️  Skipping already-converted block")
                blocks_skipped.append("Already converted")
                continue
            
            # Analyze the block
            status, reason = analyze_block(source_content, definition_content, debug)
            
            if status != 'automatable':
                if debug:
                    print(f"     ⏭️  Skipping block: {status} - {reason}")
                blocks_skipped.append(f"{status}: {reason}")
                continue
            
            # Route to appropriate converter based on language
            converter_map = {
                'yaml': (yaml_process_file, 'yaml'),
                'yml': (yaml_process_file, 'yaml'),
                'json': (json_process_file, 'json'),
                'bash': (shell_process_file, 'shell'),
                'sh': (shell_process_file, 'shell'),
                'terminal': (shell_process_file, 'shell'),
                'shell': (shell_process_file, 'shell'),
                'console': (shell_process_file, 'shell'),
                'python': (python_process_file, 'python'),
                'py': (python_process_file, 'python'),
                'go': (go_process_file, 'go'),
                'text': (generic_process_file, 'generic'),
                'conf': (generic_process_file, 'generic'),
                'config': (generic_process_file, 'generic'),
            }
            
            if language not in converter_map:
                blocks_skipped.append(f"No converter for language: {language}")
                continue
            
            converter_func, converter_type = converter_map[language]
            
            # For block-level conversion, we need to call the converter's block conversion logic
            # directly rather than process_file. Let's import the block converters:
            try:
                if converter_type == 'yaml':
                    from yaml_callout import convert_yaml_block, extract_terms_from_source as yaml_extract
                    terms = yaml_extract(source_content.splitlines())
                    # Clean source using robust cleaner
                    cleaned_lines = [clean_source_line(line, 'yaml') for line in source_content.splitlines()]
                    cleaned_source = '\n'.join(cleaned_lines)
                    new_block, complete = convert_yaml_block(match, terms, cleaned_source, debug)
                    
                elif converter_type == 'json':
                    from json_callout import convert_json_block, extract_terms_from_source as json_extract
                    terms = json_extract(source_content.splitlines())
                    # Clean source using robust cleaner
                    cleaned_lines = [clean_source_line(line, 'json') for line in source_content.splitlines()]
                    cleaned_source = '\n'.join(cleaned_lines)
                    new_block, complete = convert_json_block(match, terms, cleaned_source, debug)
                    
                elif converter_type == 'shell':
                    from shell_callout import convert_shell_block, extract_terms_from_source as shell_extract
                    terms = shell_extract(source_content.splitlines())
                    # Clean source using robust cleaner
                    cleaned_lines = [clean_source_line(line, 'shell') for line in source_content.splitlines()]
                    cleaned_source = '\n'.join(cleaned_lines)
                    new_block, complete = convert_shell_block(match, terms, cleaned_source, debug)
                    
                elif converter_type == 'python':
                    from python_callout import convert_python_block, extract_terms_from_source as python_extract
                    terms = python_extract(source_content.splitlines())
                    # Clean source using robust cleaner
                    cleaned_lines = [clean_source_line(line, 'python') for line in source_content.splitlines()]
                    cleaned_source = '\n'.join(cleaned_lines)
                    new_block, complete = convert_python_block(match, terms, cleaned_source, debug)
                    
                elif converter_type == 'go':
                    from go_callout import convert_go_block, extract_terms_from_source as go_extract
                    terms = go_extract(source_content.splitlines())
                    # Clean source using robust cleaner
                    cleaned_lines = [clean_source_line(line, 'go') for line in source_content.splitlines()]
                    cleaned_source = '\n'.join(cleaned_lines)
                    new_block, complete = convert_go_block(match, terms, cleaned_source, debug)
                    
                elif converter_type == 'generic':
                    from generic_callout import convert_generic_block, extract_terms_from_source as generic_extract
                    terms = generic_extract(source_content.splitlines())
                    # Clean source using robust cleaner
                    cleaned_lines = [clean_source_line(line, 'generic') for line in source_content.splitlines()]
                    cleaned_source = '\n'.join(cleaned_lines)
                    new_block, complete = convert_generic_block(match, terms, cleaned_source, debug)
                
                else:
                    blocks_skipped.append(f"Unknown converter type: {converter_type}")
                    continue
                
                if complete:
                    # Replace this specific block in the content
                    modified_content = modified_content.replace(original_block, new_block, 1)
                    blocks_converted += 1
                    if debug:
                        print(f"     ✓ Converted block ({language})")
                else:
                    blocks_skipped.append(f"Incomplete conversion ({language})")
                    
            except Exception as e:
                if debug:
                    print(f"     ❌ Error converting block: {e}")
                blocks_skipped.append(f"Conversion error: {e}")
                continue
        
        # Write back the modified content if anything changed
        if blocks_converted > 0 and not self.dry_run:
            try:
                with open(file_path, 'w', encoding='utf-8') as f:
                    f.write(modified_content)
            except Exception as e:
                return False, blocks_converted, blocks_skipped + [f"Write error: {e}"]
        
        return True, blocks_converted, blocks_skipped

    def convert_files(self):
        """Phase 2: Convert automatable files"""
        if not self.automatable_by_lang:
            print("\nℹ️  No files to convert automatically.")
            return
        
        print(f"\n🔄 {'[DRY RUN] ' if self.dry_run else ''}Converting files...")
        print("=" * 70)
        
        # YAML/YML
        if 'yaml' in self.automatable_by_lang or 'yml' in self.automatable_by_lang:
            yaml_files = self.automatable_by_lang.get('yaml', set()) | self.automatable_by_lang.get('yml', set())
            self._convert_language_files(yaml_files, 'yaml', yaml_process_file)
        
        # JSON
        if 'json' in self.automatable_by_lang:
            self._convert_language_files(self.automatable_by_lang['json'], 'json', json_process_file)
        
        # Shell (bash, sh, terminal)
        shell_langs = {'bash', 'sh', 'terminal', 'shell', 'console'}
        shell_files = set()
        for lang in shell_langs:
            if lang in self.automatable_by_lang:
                shell_files |= self.automatable_by_lang[lang]
        if shell_files:
            self._convert_language_files(shell_files, 'shell', shell_process_file)
        
        # Python
        python_langs = {'python', 'py'}
        python_files = set()
        for lang in python_langs:
            if lang in self.automatable_by_lang:
                python_files |= self.automatable_by_lang[lang]
        if python_files:
            self._convert_language_files(python_files, 'python', python_process_file)
        
        # Go
        go_langs = {'go', 'golang'}
        go_files = set()
        for lang in go_langs:
            if lang in self.automatable_by_lang:
                go_files |= self.automatable_by_lang[lang]
        if go_files:
            self._convert_language_files(go_files, 'go', go_process_file)
        
        # Generic (text, conf)
        generic_langs = {'text', 'conf', 'config', 'txt', 'plaintext'}
        generic_files = set()
        for lang in generic_langs:
            if lang in self.automatable_by_lang:
                generic_files |= self.automatable_by_lang[lang]
        if generic_files:
            self._convert_language_files(generic_files, 'text/conf', generic_process_file)
    
    def _convert_language_files(self, file_list, lang_name, converter_func):
        """
        Convert files using block-level processing.
        
        This ensures one bad block doesn't prevent other blocks in the same file from being converted.
        """
        print(f"\n📝 Converting {lang_name.upper()} files ({len(file_list)} files)...")
        
        for file_path in sorted(file_list):
            try:
                if self.dry_run:
                    print(f"   [DRY RUN] Would convert: {file_path}")
                    self.stats['files_converted'][lang_name] += 1
                else:
                    # Use block-level processing
                    success, blocks_converted, blocks_skipped = self.convert_file_blocks(file_path, debug=self.debug)
                    
                    if blocks_converted > 0:
                        print(f"   ✓ Converted: {file_path} ({blocks_converted} blocks)")
                        self.stats['files_converted'][lang_name] += 1
                        self.stats['blocks_converted'][lang_name] += blocks_converted
                        
                        # Log skipped blocks if any
                        if blocks_skipped and self.debug:
                            for reason in blocks_skipped:
                                print(f"      ⏭️  Skipped block: {reason}")
                    elif blocks_skipped:
                        print(f"   ⏭️  No blocks converted: {file_path}")
                        if self.debug:
                            for reason in blocks_skipped[:3]:  # Show first 3 reasons
                                print(f"      {reason}")
                    elif not success:
                        print(f"   ❌ Error: {file_path}")
                        self.stats['files_with_errors'].append((file_path, "Conversion failed"))
                    else:
                        self.stats['files_skipped']['no_callouts'].append(file_path)
                        
            except Exception as e:
                print(f"   ❌ Error: {file_path}")
                if self.debug:
                    print(f"      {e}")
                self.stats['files_with_errors'].append((file_path, str(e)))
    
    def generate_reports(self):
        """Phase 3: Generate detailed reports"""
        print(f"\n📋 Generating reports...")
        print("=" * 70)
        
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        
        # Manual review list
        if self.manual_review_files:
            manual_file = f"manual_review_{timestamp}.txt"
            with open(manual_file, 'w') as f:
                f.write("=" * 70 + "\n")
                f.write("FILES REQUIRING MANUAL REVIEW\n")
                f.write(f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
                f.write("=" * 70 + "\n\n")
                
                for issue_type, files in sorted(self.manual_review_files.items()):
                    f.write(f"\n{issue_type.replace('_', ' ').upper()}\n")
                    f.write("-" * 70 + "\n")
                    for file_path, reason in files:
                        f.write(f"  {file_path}\n")
                        f.write(f"    Reason: {reason}\n")
            
            print(f"   ✓ Manual review list: {manual_file}")
        
        # Conversion summary (JSON)
        summary_file = f"conversion_summary_{timestamp}.json"
        with open(summary_file, 'w') as f:
            summary = {
                'timestamp': datetime.now().isoformat(),
                'target_directory': str(self.target_dir),
                'dry_run': self.dry_run,
                'statistics': {
                    'total_files_scanned': self.stats['total_files_scanned'],
                    'files_with_source_blocks': self.stats['files_with_source_blocks'],
                    'files_converted': dict(self.stats['files_converted']),
                    'files_manual_review': len([f for files in self.manual_review_files.values() for f in files]),
                    'files_skipped': {k: len(v) for k, v in self.stats['files_skipped'].items()},
                    'files_with_errors': len(self.stats['files_with_errors'])
                },
                'manual_review_files': {
                    k: [{'file': f, 'reason': r} for f, r in v] 
                    for k, v in self.manual_review_files.items()
                },
                'errors': [{'file': f, 'error': e} for f, e in self.stats['files_with_errors']]
            }
            json.dump(summary, f, indent=2)
        
        print(f"   ✓ Detailed summary: {summary_file}")
    
    def print_final_summary(self):
        """Print final summary with actionable next steps"""
        print(f"\n{'=' * 70}")
        print("🎉 CONVERSION COMPLETE")
        print("=" * 70)
        
        total_converted = sum(self.stats['files_converted'].values())
        total_manual = sum(len(files) for files in self.manual_review_files.values())
        total_errors = len(self.stats['files_with_errors'])
        
        print(f"\n✅ Successfully converted: {total_converted} files")
        for lang, count in sorted(self.stats['files_converted'].items()):
            print(f"   - {lang.upper()}: {count} files")
        
        if total_manual > 0:
            print(f"\n⚠️  Requires manual review: {total_manual} files")
            print(f"   See manual_review_*.txt for details")
        
        if total_errors > 0:
            print(f"\n❌ Errors encountered: {total_errors} files")
            print(f"   See conversion_summary_*.json for details")
        
        if self.dry_run:
            print(f"\nℹ️  DRY RUN MODE - No files were modified")
            print(f"   Run without --dry-run to apply changes")
        
        print()
    
    def run(self):
        """Main orchestrator workflow"""
        # Validate
        if not self.validate_environment():
            return 1
        
        # Single file mode - simplified workflow
        if self.single_file_mode:
            return self.run_single_file()
        
        # Phase 1: Scan and classify
        self.scan_and_classify()
        
        # Phase 2: Convert
        self.convert_files()
        
        # Phase 3: Generate reports
        self.generate_reports()
        
        # Final summary
        self.print_final_summary()
        
        return 0
    
    def run_single_file(self):
        """Process a single file directly"""
        file_path = self.target_path
        print(f"🔍 Processing single file: {file_path}")
        print("=" * 70)
        
        # Validate it's an AsciiDoc file with callouts
        if not self.is_valid_adoc_file(file_path):
            print(f"❌ Error: Invalid or unreadable file")
            return 1
        
        # Classify the file
        automatable_langs, manual_issues, error = self.classify_file(file_path)
        
        if error:
            print(f"❌ Error analyzing file: {error}")
            return 1
        
        if automatable_langs is None and manual_issues is None:
            print(f"ℹ️  No source blocks with callouts found in file")
            return 0
        
        # Report what we found
        print(f"\n📊 Analysis Results:")
        if automatable_langs:
            print(f"   ✅ Automatable blocks: {', '.join(sorted(automatable_langs)).upper()}")
        if manual_issues:
            print(f"   ⚠️  Issues found:")
            for issue_type, reason in manual_issues:
                print(f"      - {issue_type}: {reason}")
        
        # Convert if there are automatable blocks
        if automatable_langs:
            if self.dry_run:
                print(f"\n🔄 [DRY RUN] Would convert file")
                print(f"   Languages: {', '.join(sorted(automatable_langs)).upper()}")
            else:
                print(f"\n🔄 Converting...")
                success, blocks_converted, blocks_skipped = self.convert_file_blocks(file_path, debug=self.debug)
                
                if blocks_converted > 0:
                    print(f"   ✅ Successfully converted {blocks_converted} block(s)")
                    if blocks_skipped:
                        print(f"   ⏭️  Skipped {len(blocks_skipped)} block(s)")
                        if self.debug:
                            for reason in blocks_skipped:
                                print(f"      - {reason}")
                else:
                    print(f"   ⚠️  No blocks were converted")
                    if blocks_skipped:
                        print(f"   Reasons:")
                        for reason in blocks_skipped[:5]:  # Show first 5
                            print(f"      - {reason}")
        else:
            print(f"\nℹ️  No automatable blocks found. Manual review required.")
        
        print()
        return 0


def main():
    parser = argparse.ArgumentParser(
        description="Unified callouts conversion orchestrator - automatically converts AsciiDoc callouts to definition lists",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  Single File:
  %(prog)s /path/to/file.adoc                # Convert a single file
  %(prog)s /path/to/file.adoc --dry-run      # Preview changes for a single file

  Directory:
  %(prog)s /path/to/docs/                    # Convert all files in directory
  %(prog)s /path/to/docs/ --dry-run          # Preview without changes
  %(prog)s /path/to/docs/ --debug            # Show detailed debug info
  %(prog)s .                                 # Convert current directory

  Assembly Mode (recommended for doc repos with shared modules):
  %(prog)s /path/to/assembly-dir/ --assembly-mode
  
  This mode:
  1. Finds all assembly files in the target directory
  2. Parses include:: directives to find referenced modules
  3. Only converts the modules that are actually included
  4. Ignores the thousands of other modules in shared directories
        """
    )
    
    parser.add_argument(
        'target_path',
        help='File or directory to process (single .adoc file or directory of files)'
    )
    
    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='Preview changes without modifying files'
    )
    
    parser.add_argument(
        '--debug',
        action='store_true',
        help='Enable debug output'
    )
    
    parser.add_argument(
        '--assembly-mode',
        action='store_true',
        help='Only convert modules referenced by assemblies in target directory. '
             'Use this when your target directory contains assemblies that include '
             'modules from a shared modules/ directory.'
    )
    
    args = parser.parse_args()
    
    print("=" * 70)
    print("ASCIIDOC CALLOUTS CONVERSION ORCHESTRATOR")
    print("=" * 70)
    
    target = Path(args.target_path).resolve()
    if target.is_file():
        print(f"📦 Mode: SINGLE FILE")
    elif args.assembly_mode:
        print("📦 Mode: ASSEMBLY (only converting referenced modules)")
    else:
        print("📦 Mode: DEFAULT (converting all files)")
    print("=" * 70)
    
    orchestrator = CalloutsOrchestrator(
        target_path=args.target_path,
        dry_run=args.dry_run,
        debug=args.debug,
        assembly_mode=args.assembly_mode
    )
    
    return orchestrator.run()


if __name__ == "__main__":
    sys.exit(main())

