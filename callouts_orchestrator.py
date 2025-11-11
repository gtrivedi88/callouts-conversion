#!/usr/bin/env python3
"""
Unified Callouts Conversion Orchestrator

This script automatically:
1. Scans AsciiDoc files for code blocks with callouts
2. Classifies them as automatable vs. manual review needed
3. Routes to appropriate converters (YAML, JSON, bash, etc.)
4. Generates comprehensive reports
"""

import sys
import os
import argparse
from pathlib import Path
from collections import defaultdict
import json
from datetime import datetime

# Import our modules
try:
    from granular_callput import analyze_block, get_callout_pattern, SUPPORTED_LANGUAGES
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
    def __init__(self, target_dir, dry_run=False, debug=False):
        self.target_dir = Path(target_dir).resolve()
        self.dry_run = dry_run
        self.debug = debug
        
        # Statistics
        self.stats = {
            'total_files_scanned': 0,
            'files_with_source_blocks': 0,
            'files_converted': defaultdict(int),
            'files_manual_review': defaultdict(list),
            'files_skipped': defaultdict(list),
            'files_with_errors': [],
            'blocks_converted': defaultdict(int)
        }
        
        # Classification results
        self.automatable_by_lang = defaultdict(set)
        self.manual_review_files = defaultdict(list)
        
    def validate_environment(self):
        """Validate the target directory exists and is accessible"""
        if not self.target_dir.exists():
            print(f"❌ Error: Target directory does not exist: {self.target_dir}")
            return False
        
        if not self.target_dir.is_dir():
            print(f"❌ Error: Target path is not a directory: {self.target_dir}")
            return False
        
        if not os.access(self.target_dir, os.R_OK):
            print(f"❌ Error: No read permission for directory: {self.target_dir}")
            return False
        
        return True
    
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
        
        pattern = get_callout_pattern()
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
        
        for root, dirs, files in os.walk(self.target_dir):
            # Skip hidden directories
            dirs[:] = [d for d in dirs if not d.startswith('.')]
            
            for filename in files:
                if not filename.lower().endswith(('.adoc', '.asciidoc')):
                    continue
                
                file_path = Path(root) / filename
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
                
                # Categorize
                if manual_issues:
                    # Has issues - needs manual review
                    for issue_type, reason in manual_issues:
                        self.manual_review_files[issue_type].append((str(file_path), reason))
                elif automatable_langs:
                    # Clean for automation
                    for lang in automatable_langs:
                        self.automatable_by_lang[lang].add(str(file_path))
        
        # Print classification summary
        self._print_classification_summary()
    
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
        """Convert files for a specific language using the appropriate converter"""
        print(f"\n📝 Converting {lang_name.upper()} files ({len(file_list)} files)...")
        
        for file_path in sorted(file_list):
            try:
                if self.dry_run:
                    print(f"   [DRY RUN] Would convert: {file_path}")
                    self.stats['files_converted'][lang_name] += 1
                else:
                    # Use the language-specific converter
                    success, warnings = converter_func(file_path, debug=self.debug)
                    
                    if success:
                        print(f"   ✓ Converted: {file_path}")
                        self.stats['files_converted'][lang_name] += 1
                    elif warnings:
                        print(f"   ⚠ Incomplete: {file_path}")
                        self.manual_review_files['incomplete_conversion'].append((file_path, 'Incomplete conversion'))
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
        
        # Phase 1: Scan and classify
        self.scan_and_classify()
        
        # Phase 2: Convert
        self.convert_files()
        
        # Phase 3: Generate reports
        self.generate_reports()
        
        # Final summary
        self.print_final_summary()
        
        return 0


def main():
    parser = argparse.ArgumentParser(
        description="Unified callouts conversion orchestrator - automatically converts AsciiDoc callouts to definition lists",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s /path/to/docs/                    # Convert all files
  %(prog)s /path/to/docs/ --dry-run          # Preview without changes
  %(prog)s /path/to/docs/ --debug            # Show detailed debug info
  %(prog)s .                                 # Convert current directory
        """
    )
    
    parser.add_argument(
        'target_dir',
        help='Directory to scan for AsciiDoc files'
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
    
    args = parser.parse_args()
    
    print("=" * 70)
    print("ASCIIDOC CALLOUTS CONVERSION ORCHESTRATOR")
    print("=" * 70)
    
    orchestrator = CalloutsOrchestrator(
        target_dir=args.target_dir,
        dry_run=args.dry_run,
        debug=args.debug
    )
    
    return orchestrator.run()


if __name__ == "__main__":
    sys.exit(main())

