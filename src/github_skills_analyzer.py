#!/usr/bin/env python3
"""
GitHub Skills Analyzer
A proof-of-concept tool for analyzing developer skills from GitHub activity
to generate personalized learning recommendations.
"""

import asyncio
import json
import logging
import os
import sys
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, asdict
from collections import defaultdict, Counter

import requests
import openai
from openai import OpenAI
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

@dataclass
class CommitData:
    """Structure for commit information"""
    sha: str
    message: str
    date: datetime
    additions: int
    deletions: int
    files_changed: List[str]
    repo_name: str

@dataclass
class RepositoryData:
    """Structure for repository information"""
    name: str
    language: str
    stars: int
    description: str
    created_at: datetime
    updated_at: datetime
    size: int
    has_readme: bool
    readme_quality_score: float

@dataclass
class CollaborationData:
    """Structure for collaboration metrics"""
    pull_requests_created: int
    pull_requests_reviewed: int
    issues_created: int
    issues_commented: int
    repos_contributed_to: int
    collaboration_score: float

@dataclass
class SkillAssessment:
    """AI-generated skill assessment"""
    technical_skills: Dict[str, float]
    strengths: List[str]
    improvement_areas: List[str]
    experience_level: str
    specializations: List[str]
    learning_style: str
    confidence_score: float

class GitHubAnalyzer:
    """Main analyzer class for GitHub data"""
    
    def __init__(self, username: str, github_token: Optional[str] = None):
        self.username = username
        self.base_url = "https://api.github.com"
        self.headers = {
            'Accept': 'application/vnd.github.v3+json',
            'User-Agent': 'GitHub-Skills-Analyzer/1.0'
        }
        
        # Use provided token or get from environment
        token = github_token or os.getenv('GITHUB_TOKEN')
        if token:
            self.headers['Authorization'] = f'token {token}'
            logger.info("Using authenticated GitHub API (higher rate limits)")
        else:
            logger.warning("No GitHub token provided - using public API with rate limits")
            
        self.session = requests.Session()
        self.session.headers.update(self.headers)
        
    def _make_request(self, endpoint: str) -> Optional[Dict]:
        """Make API request with error handling"""
        url = f"{self.base_url}{endpoint}"
        try:
            response = self.session.get(url)
            response.raise_for_status()
            
            # Check rate limit and show more detailed info
            remaining = int(response.headers.get('X-RateLimit-Remaining', 0))
            reset_time = int(response.headers.get('X-RateLimit-Reset', 0))
            if remaining < 10:
                reset_datetime = datetime.fromtimestamp(reset_time)
                logger.warning(f"Rate limit low: {remaining} requests remaining (resets at {reset_datetime})")
            elif remaining < 100:
                logger.info(f"Rate limit status: {remaining} requests remaining")
                
            return response.json()
        except requests.exceptions.RequestException as e:
            logger.error(f"API request failed: {e}")
            return None
    
    def get_user_profile(self) -> Dict:
        """Fetch user profile information"""
        logger.info(f"Fetching profile for {self.username}")
        return self._make_request(f"/users/{self.username}")
    
    def get_repositories(self) -> List[Dict]:
        """Fetch user repositories"""
        logger.info("Fetching repositories")
        repos = []
        page = 1
        
        while True:
            data = self._make_request(f"/users/{self.username}/repos?per_page=100&page={page}")
            if not data or len(data) == 0:
                break
                
            repos.extend(data)
            page += 1
            
            # Limit to avoid rate limits in demo
            if len(repos) >= 300:
                break
                
        logger.info(f"Found {len(repos)} repositories")
        return repos
    
    def get_commits_for_repo(self, repo_name: str, since_date: datetime) -> List[Dict]:
        """Fetch commits for a specific repository"""
        commits = []
        page = 1
        
        while True:
            endpoint = f"/repos/{self.username}/{repo_name}/commits"
            # Remove author parameter - it may be filtering out commits
            # GitHub API will return all commits, we'll filter by author later
            params = f"?since={since_date.isoformat()}&per_page=100&page={page}"
            
            
            data = self._make_request(endpoint + params)
            if not data or len(data) == 0:
                break
            
            # Filter commits by author after fetching
            user_commits = []
            for commit in data:
                is_user_commit = False
                author_login = None
                author_email = None
                
                # Check GitHub user login first
                if commit.get('author'):
                    author_login = commit['author'].get('login')
                    if author_login == self.username:
                        is_user_commit = True
                
                # If no GitHub user, check by email in commit data
                if not is_user_commit and commit.get('commit', {}).get('author'):
                    author_email = commit['commit']['author'].get('email', '')
                    # For repositories owned by the user, assume commits are theirs
                    # This is a reasonable assumption since we're only looking at their repos
                    if author_email:
                        is_user_commit = True
                
                
                if is_user_commit:
                    user_commits.append(commit)
            
            commits.extend(user_commits)
            page += 1
            
            # Limit to avoid excessive API calls
            if len(commits) >= 200:
                break
                
        return commits
    
    def get_commit_details(self, repo_name: str, sha: str) -> Optional[Dict]:
        """Get detailed commit information"""
        return self._make_request(f"/repos/{self.username}/{repo_name}/commits/{sha}")
    
    def get_readme_content(self, repo_name: str) -> Optional[str]:
        """Fetch README content for documentation analysis"""
        try:
            data = self._make_request(f"/repos/{self.username}/{repo_name}/readme")
            if data and 'content' in data:
                import base64
                content = base64.b64decode(data['content']).decode('utf-8')
                return content
        except Exception as e:
            logger.debug(f"No README found for {repo_name}: {e}")
        return None
    
    def analyze_repository_activity(self, repos: List[Dict], months: int = 6) -> List[CommitData]:
        """Analyze commit activity across repositories with temporal progression focus"""
        logger.info(f"Analyzing commit activity for last {months} months")
        since_date = datetime.now() - timedelta(days=months * 30)
        logger.info(f"Searching for commits since: {since_date.isoformat()}")
        all_commits = []
        
        # Sort repos by activity (most recent first) - EXACTLY like original working version
        active_repos = [r for r in repos if r.get('updated_at')]
        active_repos.sort(key=lambda x: x['updated_at'], reverse=True)
        
        # Analyze top repositories to stay within rate limits (original working approach)
        selected_repos = active_repos[:20]  # Limit to 20 most active repos
        
        # Analyze selected repositories
        for repo in selected_repos:
            repo_name = repo['name']
            logger.info(f"Analyzing commits in {repo_name}")
            
            commits = self.get_commits_for_repo(repo_name, since_date)
            logger.info(f"Found {len(commits)} commits in {repo_name}")
            
            for commit_summary in commits[:50]:  # Limit commits per repo
                # Get detailed commit info
                commit_detail = self.get_commit_details(repo_name, commit_summary['sha'])
                if not commit_detail:
                    continue
                
                files_changed = []
                additions = 0
                deletions = 0
                
                if 'files' in commit_detail:
                    for file_info in commit_detail['files']:
                        files_changed.append(file_info['filename'])
                        additions += file_info.get('additions', 0)
                        deletions += file_info.get('deletions', 0)
                
                commit_data = CommitData(
                    sha=commit_summary['sha'],
                    message=commit_detail['commit']['message'],
                    date=datetime.fromisoformat(commit_detail['commit']['author']['date'].replace('Z', '+00:00')),
                    additions=additions,
                    deletions=deletions,
                    files_changed=files_changed,
                    repo_name=repo_name
                )
                
                all_commits.append(commit_data)
        
        logger.info(f"Collected {len(all_commits)} commits for analysis")
        return all_commits
    
    def analyze_documentation_quality(self, repos: List[Dict]) -> List[RepositoryData]:
        """Analyze documentation quality across repositories"""
        logger.info("Analyzing documentation quality")
        repo_data = []
        
        for repo in repos[:10]:  # Analyze top 10 repos
            readme_content = self.get_readme_content(repo['name'])
            readme_score = self._score_readme_quality(readme_content) if readme_content else 0
            
            repo_info = RepositoryData(
                name=repo['name'],
                language=repo.get('language', 'Unknown'),
                stars=repo.get('stargazers_count', 0),
                description=repo.get('description', ''),
                created_at=datetime.fromisoformat(repo['created_at'].replace('Z', '+00:00')),
                updated_at=datetime.fromisoformat(repo['updated_at'].replace('Z', '+00:00')),
                size=repo.get('size', 0),
                has_readme=readme_content is not None,
                readme_quality_score=readme_score
            )
            
            repo_data.append(repo_info)
        
        return repo_data
    
    def _score_readme_quality(self, content: str) -> float:
        """Score README quality (basic heuristic)"""
        if not content:
            return 0.0
        
        score = 0.0
        content_lower = content.lower()
        
        # Basic quality indicators
        if len(content) > 500:
            score += 0.2
        if 'installation' in content_lower or 'setup' in content_lower:
            score += 0.2
        if 'usage' in content_lower or 'example' in content_lower:
            score += 0.2
        if '```' in content:  # Code examples
            score += 0.2
        if content.count('#') >= 3:  # Multiple sections
            score += 0.2
        
        return min(score, 1.0)
    
    def analyze_collaboration_patterns(self, repos: List[Dict]) -> CollaborationData:
        """Analyze collaboration and community engagement"""
        logger.info("Analyzing collaboration patterns")
        
        # This would require additional API calls for PRs, issues, etc.
        # For the demo, we'll use repository data to estimate collaboration
        
        total_repos = len(repos)
        public_repos = len([r for r in repos if not r.get('private', True)])
        forked_repos = len([r for r in repos if r.get('fork', False)])
        original_repos = public_repos - forked_repos
        
        # Calculate a basic collaboration score
        collaboration_score = min((forked_repos * 0.1 + original_repos * 0.3) / max(total_repos, 1), 1.0)
        
        return CollaborationData(
            pull_requests_created=0,  # Would need additional API calls
            pull_requests_reviewed=0,
            issues_created=0,
            issues_commented=0,
            repos_contributed_to=forked_repos,
            collaboration_score=collaboration_score
        )

class AISkillsAnalyzer:
    """AI-powered skills analysis using OpenAI GPT-4"""
    
    def __init__(self, api_key: str):
        self.client = OpenAI(api_key=api_key)
    
    def analyze_commits(self, commits: List[CommitData]) -> SkillAssessment:
        """Analyze commits using AI to extract skill insights with temporal progression"""
        logger.info(f"Analyzing {len(commits)} commits with AI")
        
        # Sort commits by date for temporal analysis
        commits_sorted = sorted(commits, key=lambda c: c.date)
        
        # Split into early and recent periods for progression analysis
        mid_point = len(commits_sorted) // 2
        early_commits = commits_sorted[:mid_point] if mid_point > 0 else []
        recent_commits = commits_sorted[mid_point:] if mid_point > 0 else commits_sorted
        
        # Prepare commit data for analysis
        commit_summaries = []
        language_usage = Counter()
        temporal_analysis = {
            'early_period': {'commits': [], 'languages': Counter()},
            'recent_period': {'commits': [], 'languages': Counter()}
        }
        
        # Process all commits
        for commit in commits[:100]:  # Limit for API efficiency
            # Extract programming languages from file extensions
            for file in commit.files_changed:
                ext = file.split('.')[-1].lower() if '.' in file else 'unknown'
                language_usage[ext] += 1
            
            commit_summary = {
                'message': commit.message[:200],  # Truncate long messages
                'additions': commit.additions,
                'deletions': commit.deletions,
                'files': len(commit.files_changed),
                'repo': commit.repo_name,
                'date': commit.date.isoformat()
            }
            commit_summaries.append(commit_summary)
            
            # Categorize by time period
            if commit in early_commits:
                temporal_analysis['early_period']['commits'].append(commit_summary)
                for file in commit.files_changed:
                    ext = file.split('.')[-1].lower() if '.' in file else 'unknown'
                    temporal_analysis['early_period']['languages'][ext] += 1
            else:
                temporal_analysis['recent_period']['commits'].append(commit_summary)
                for file in commit.files_changed:
                    ext = file.split('.')[-1].lower() if '.' in file else 'unknown'
                    temporal_analysis['recent_period']['languages'][ext] += 1
        
        # Create enhanced analysis prompt with temporal context
        prompt = self._create_temporal_analysis_prompt(commit_summaries, language_usage, temporal_analysis)
        
        try:
            response = self.client.chat.completions.create(
                model="gpt-4-turbo-preview",
                messages=[
                    {"role": "system", "content": self._get_system_prompt()},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.2,
                max_tokens=2000
            )
            
            # Parse AI response
            analysis_text = response.choices[0].message.content
            return self._parse_ai_response(analysis_text, language_usage)
            
        except Exception as e:
            logger.error(f"AI analysis failed: {e}")
            return self._create_fallback_assessment(language_usage)
    
    def _get_system_prompt(self) -> str:
        """Get system prompt for AI analysis"""
        return """You are a senior software engineering mentor analyzing a developer's GitHub activity to provide personalized learning recommendations. 

Analyze the commit patterns, code changes, and project types to assess:
1. Technical skill levels across different technologies
2. Code quality and engineering practices
3. Areas of strength and expertise
4. Potential skill gaps and improvement opportunities
5. Learning style preferences based on commit patterns
6. Overall experience level

Provide specific, actionable insights that would help this developer grow their skills. Be encouraging but honest about areas for improvement."""
    
    def _create_temporal_analysis_prompt(self, commits: List[Dict], language_usage: Counter, temporal_analysis: Dict) -> str:
        """Create detailed analysis prompt with temporal progression focus"""
        
        prompt = f"""Analyze this developer's GitHub activity with focus on SKILL PROGRESSION over time:

OVERALL LANGUAGE USAGE (by file extensions):
{dict(language_usage.most_common(10))}

TEMPORAL SKILL PROGRESSION:
Early Period Languages: {dict(temporal_analysis['early_period']['languages'].most_common(5))}
Recent Period Languages: {dict(temporal_analysis['recent_period']['languages'].most_common(5))}

EARLY PERIOD COMMITS ({len(temporal_analysis['early_period']['commits'])} commits):
"""
        
        for i, commit in enumerate(temporal_analysis['early_period']['commits'][:10]):
            prompt += f"{i+1}. [{commit['date'][:10]}] {commit['message']}\n   Files: {commit['files']}, +{commit['additions']}/-{commit['deletions']} lines, Repo: {commit['repo']}\n"
        
        prompt += f"""

RECENT PERIOD COMMITS ({len(temporal_analysis['recent_period']['commits'])} commits):
"""
        
        for i, commit in enumerate(temporal_analysis['recent_period']['commits'][:10]):
            prompt += f"{i+1}. [{commit['date'][:10]}] {commit['message']}\n   Files: {commit['files']}, +{commit['additions']}/-{commit['deletions']} lines, Repo: {commit['repo']}\n"
        
        prompt += f"""

ANALYSIS REQUIREMENTS:
Please provide a structured analysis focusing on SKILL DEVELOPMENT OVER TIME:

1. TECHNICAL SKILLS PROGRESSION: 
   - Rate current proficiency (1-10) in identified technologies
   - Identify skills that have IMPROVED over the timeframe
   - Note any new technologies adopted recently

2. LEARNING TRAJECTORY:
   - What patterns show growth and development?
   - Are they expanding into new areas or deepening existing skills?
   - Evidence of increasing complexity in projects?

3. CURRENT STRENGTHS: What this developer does well NOW

4. IMPROVEMENT AREAS: Skills to develop based on recent patterns

5. EXPERIENCE LEVEL: Junior/Mid/Senior assessment based on progression

6. SPECIALIZATION EVOLUTION: How their focus areas have changed

7. LEARNING STYLE: Preferences based on temporal patterns

8. RECOMMENDATIONS: Next steps based on their development trajectory

Format your response clearly with these sections, emphasizing GROWTH and PROGRESSION."""
        
        return prompt
    
    def _create_analysis_prompt(self, commits: List[Dict], language_usage: Counter) -> str:
        """Fallback analysis prompt for backwards compatibility"""
        return self._create_temporal_analysis_prompt(commits, language_usage, {
            'early_period': {'commits': commits[:len(commits)//2], 'languages': Counter()},
            'recent_period': {'commits': commits[len(commits)//2:], 'languages': Counter()}
        })
    
    def _parse_ai_response(self, analysis_text: str, language_usage: Counter) -> SkillAssessment:
        """Parse AI response into structured assessment"""
        
        # Define non-programming file extensions to exclude from skill assessment
        excluded_extensions = {
            'json', 'xml', 'yaml', 'yml', 'toml', 'ini', 'cfg', 'conf',  # Config files
            'png', 'jpg', 'jpeg', 'gif', 'svg', 'ico', 'bmp', 'webp',    # Images
            'pdf', 'doc', 'docx', 'txt', 'rtf',                          # Documents
            'md', 'rst', 'html', 'htm',                                   # Markup/docs
            'csv', 'tsv', 'xlsx', 'xls',                                 # Data files
            'zip', 'tar', 'gz', 'rar', '7z',                            # Archives
            'log', 'tmp', 'cache', 'lock',                               # System files
            'gitignore', 'gitkeep', 'env', 'example'                     # Other non-code
        }
        
        # Extract technical skills (basic parsing) - exclude non-programming extensions
        technical_skills = {}
        for lang, count in language_usage.most_common(10):
            # Skip non-programming file extensions
            if lang.lower() in excluded_extensions:
                continue
                
            # Estimate proficiency based on usage frequency
            max_count = language_usage.most_common(1)[0][1] if language_usage else 1
            proficiency = min(10, (count / max_count) * 10)
            technical_skills[lang] = round(proficiency, 1)
        
        # Extract sections from AI response (simplified parsing)
        strengths = []
        improvements = []
        
        lines = analysis_text.split('\n')
        current_section = None
        
        for line in lines:
            line = line.strip()
            if 'STRENGTHS' in line.upper():
                current_section = 'strengths'
            elif 'IMPROVEMENT' in line.upper() or 'AREAS' in line.upper():
                current_section = 'improvements'
            elif line.startswith('- ') or line.startswith('• '):
                if current_section == 'strengths':
                    strengths.append(line[2:])
                elif current_section == 'improvements':
                    improvements.append(line[2:])
        
        # Determine experience level based on activity
        total_commits = sum(language_usage.values())
        if total_commits < 50:
            experience = "Junior"
        elif total_commits < 200:
            experience = "Mid-level"
        else:
            experience = "Senior"
        
        return SkillAssessment(
            technical_skills=technical_skills,
            strengths=strengths[:5],  # Top 5 strengths
            improvement_areas=improvements[:5],  # Top 5 areas
            experience_level=experience,
            specializations=list(language_usage.most_common(3)),
            learning_style="Inferred from commit patterns",
            confidence_score=0.8  # AI confidence in assessment
        )
    
    def _create_fallback_assessment(self, language_usage: Counter) -> SkillAssessment:
        """Create basic assessment if AI analysis fails"""
        technical_skills = {}
        for lang, count in language_usage.most_common(5):
            technical_skills[lang] = min(10, count / 10)
        
        return SkillAssessment(
            technical_skills=technical_skills,
            strengths=["Active contributor", "Multi-language experience"],
            improvement_areas=["Detailed analysis requires OpenAI API"],
            experience_level="Unable to determine",
            specializations=list(language_usage.most_common(2)),
            learning_style="Unable to determine",
            confidence_score=0.3
        )

class ReportGenerator:
    """Generate analysis reports in multiple formats"""
    
    def __init__(self, username: str):
        self.username = username
        self.timestamp = datetime.now()
    
    def generate_json_report(self, 
                           profile: Dict,
                           commits: List[CommitData],
                           repos: List[RepositoryData],
                           collaboration: CollaborationData,
                           skills: SkillAssessment) -> Dict:
        """Generate comprehensive JSON report"""
        
        return {
            "metadata": {
                "username": self.username,
                "analysis_date": self.timestamp.isoformat(),
                "commits_analyzed": len(commits),
                "repositories_analyzed": len(repos)
            },
            "profile": profile,
            "activity_summary": {
                "total_commits": len(commits),
                "total_additions": sum(c.additions for c in commits),
                "total_deletions": sum(c.deletions for c in commits),
                "active_repositories": len(set(c.repo_name for c in commits)),
                "most_active_repo": max(commits, key=lambda c: c.additions).repo_name if commits else None
            },
            "technical_skills": asdict(skills),
            "repository_analysis": [asdict(repo) for repo in repos],
            "collaboration_metrics": asdict(collaboration),
            "learning_recommendations": self._generate_learning_recommendations(skills)
        }
    
    def generate_markdown_report(self, json_data: Dict) -> str:
        """Generate human-readable markdown report"""
        
        md = f"""# GitHub Skills Analysis Report
        
**Developer:** {json_data['metadata']['username']}  
**Analysis Date:** {json_data['metadata']['analysis_date'][:10]}  
**Commits Analyzed:** {json_data['metadata']['commits_analyzed']}  

## 📊 Activity Summary

- **Total Commits:** {json_data['activity_summary']['total_commits']}
- **Lines Added:** {json_data['activity_summary']['total_additions']:,}
- **Lines Removed:** {json_data['activity_summary']['total_deletions']:,}
- **Active Repositories:** {json_data['activity_summary']['active_repositories']}
- **Most Active Repository:** {json_data['activity_summary']['most_active_repo'] or 'N/A'}

## 🛠️ Technical Skills Assessment

"""
        
        skills = json_data['technical_skills']
        if skills['technical_skills']:
            md += "| Technology | Proficiency (1-10) |\n|------------|--------------------|\n"
            for tech, score in skills['technical_skills'].items():
                md += f"| {tech} | {score}/10 |\n"
        
        md += f"""

## ✅ Strengths

"""
        for strength in skills['strengths']:
            md += f"- {strength}\n"
        
        md += f"""

## 📈 Areas for Improvement

"""
        for area in skills['improvement_areas']:
            md += f"- {area}\n"
        
        md += f"""

## 🎯 Developer Profile

- **Experience Level:** {skills['experience_level']}
- **Primary Specializations:** {', '.join([str(s) for s in skills['specializations']])}
- **Learning Style:** {skills['learning_style']}
- **Assessment Confidence:** {skills['confidence_score']*100:.1f}%

## 📚 Learning Recommendations

"""
        
        for rec in json_data['learning_recommendations']:
            md += f"### {rec['category']}\n"
            md += f"**Priority:** {rec['priority']}\n\n"
            for item in rec['items']:
                md += f"- {item}\n"
            md += "\n"
        
        md += f"""

## 📁 Repository Analysis

| Repository | Language | Stars | Documentation | Last Updated |
|------------|----------|-------|---------------|--------------|
"""
        
        for repo in json_data['repository_analysis']:
            doc_score = f"{repo['readme_quality_score']*100:.0f}%" if repo['has_readme'] else "None"
            updated_at = repo['updated_at'].strftime('%Y-%m-%d') if isinstance(repo['updated_at'], datetime) else str(repo['updated_at'])[:10]
            md += f"| {repo['name']} | {repo['language']} | {repo['stars']} | {doc_score} | {updated_at} |\n"
        
        return md
    
    def _generate_learning_recommendations(self, skills: SkillAssessment) -> List[Dict]:
        """Generate personalized learning recommendations"""
        recommendations = []
        
        # Technical skill recommendations
        if skills.improvement_areas:
            recommendations.append({
                "category": "Technical Skills Development",
                "priority": "High",
                "items": [
                    f"Focus on: {area}" for area in skills.improvement_areas[:3]
                ]
            })
        
        # General recommendations based on experience level
        if skills.experience_level == "Junior":
            recommendations.append({
                "category": "Foundation Building",
                "priority": "High",
                "items": [
                    "Master version control best practices",
                    "Learn test-driven development",
                    "Study software design patterns",
                    "Practice code review skills"
                ]
            })
        elif skills.experience_level == "Mid-level":
            recommendations.append({
                "category": "Advanced Development",
                "priority": "Medium",
                "items": [
                    "Explore system architecture patterns",
                    "Contribute to open source projects",
                    "Mentor junior developers",
                    "Learn about DevOps practices"
                ]
            })
        
        # Documentation recommendations
        recommendations.append({
            "category": "Communication & Documentation",
            "priority": "Medium",
            "items": [
                "Improve README quality with examples",
                "Write technical blog posts",
                "Create architecture decision records",
                "Practice explaining complex concepts simply"
            ]
        })
        
        return recommendations
    
    def save_reports(self, json_data: Dict, output_dir: str = "reports"):
        """Save both JSON and Markdown reports"""
        
        # Ensure output directory exists
        os.makedirs(output_dir, exist_ok=True)
        
        timestamp_str = self.timestamp.strftime("%Y%m%d_%H%M%S")
        
        # Save JSON report
        json_filename = f"{output_dir}/github_analysis_{self.username}_{timestamp_str}.json"
        with open(json_filename, 'w') as f:
            json.dump(json_data, f, indent=2, default=str)
        
        # Save Markdown report
        markdown_content = self.generate_markdown_report(json_data)
        md_filename = f"{output_dir}/github_analysis_{self.username}_{timestamp_str}.md"
        with open(md_filename, 'w') as f:
            f.write(markdown_content)
        
        logger.info(f"Reports saved: {json_filename} and {md_filename}")
        return json_filename, md_filename

async def main():
    """Main analysis function"""
    
    # Configuration from environment variables
    USERNAME = os.getenv('GITHUB_USERNAME', 'dang-w')
    GITHUB_TOKEN = os.getenv('GITHUB_TOKEN')
    OPENAI_API_KEY = os.getenv('OPENAI_API_KEY')
    ANALYSIS_MONTHS = int(os.getenv('ANALYSIS_MONTHS', 12))  # Increased to 12 months
    
    # Validation
    if not OPENAI_API_KEY:
        logger.error("OPENAI_API_KEY environment variable required")
        print("\n❌ Missing OpenAI API Key!")
        print("Please add to .env file: OPENAI_API_KEY=your-api-key-here")
        return
        
    if not GITHUB_TOKEN:
        logger.warning("GITHUB_TOKEN not provided - will use public API with lower rate limits")
        print("💡 For better performance, add GitHub token to .env file: GITHUB_TOKEN=your-token-here")
        print("   Get a token at: https://github.com/settings/tokens")
        print("   Required permissions: 'public_repo' for public repos, 'repo' for private repos\n")
    
    try:
        # Initialize analyzers
        github_analyzer = GitHubAnalyzer(USERNAME, GITHUB_TOKEN)
        ai_analyzer = AISkillsAnalyzer(OPENAI_API_KEY)
        report_generator = ReportGenerator(USERNAME)
        
        # Step 1: Get user profile
        logger.info("Starting GitHub analysis...")
        profile = github_analyzer.get_user_profile()
        if not profile:
            logger.error("Failed to fetch user profile")
            return
        
        # Step 2: Analyze repositories
        repos = github_analyzer.get_repositories()
        repo_data = github_analyzer.analyze_documentation_quality(repos)
        
        # Step 3: Analyze commit activity
        commits = github_analyzer.analyze_repository_activity(repos, months=ANALYSIS_MONTHS)
        if not commits:
            logger.warning("No commits found in the last 6 months")
        
        # Step 4: Analyze collaboration patterns
        collaboration = github_analyzer.analyze_collaboration_patterns(repos)
        
        # Step 5: AI-powered skills assessment
        logger.info("Running AI analysis... (this may take a moment)")
        skills_assessment = ai_analyzer.analyze_commits(commits)
        
        # Step 6: Generate reports
        json_data = report_generator.generate_json_report(
            profile, commits, repo_data, collaboration, skills_assessment
        )
        
        # Step 7: Save reports
        json_file, md_file = report_generator.save_reports(json_data)
        
        # Step 8: Display summary
        print(f"\n🎉 Analysis Complete!")
        print(f"📊 Analyzed {len(commits)} commits across {len(repos)} repositories")
        print(f"📄 Reports saved:")
        print(f"   JSON: {json_file}")
        print(f"   Markdown: {md_file}")
        
        print(f"\n📈 Quick Summary:")
        print(f"   Experience Level: {skills_assessment.experience_level}")
        print(f"   Top Skills: {list(skills_assessment.technical_skills.keys())[:3]}")
        print(f"   Confidence Score: {skills_assessment.confidence_score*100:.1f}%")
        
    except Exception as e:
        logger.error(f"Analysis failed: {e}")
        raise

if __name__ == "__main__":
    # Check for required dependencies
    try:
        import requests
        import openai
    except ImportError as e:
        print(f"Missing required dependency: {e}")
        print("Install with: pip install requests openai")
        sys.exit(1)
    
    asyncio.run(main())