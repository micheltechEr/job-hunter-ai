import re
from typing import Dict, List, Set, Tuple, Optional
from pydantic import BaseModel, Field


class TechTaxonomy(BaseModel):
    canonical_id: str
    display_name: str
    category: str # language, framework, database, devops, cloud, domain
    ecosystem: str # jvm, dotnet, python, js_ts, php, golang, rust, ruby, mobile, data, ops
    aliases: List[str] = Field(default_factory=list)


# Comprehensive tech taxonomy with explicit disambiguation rules
TECH_TAXONOMY_REGISTRY: List[TechTaxonomy] = [
    # JVM Ecosystem (Explicitly disjoint from JS)
    TechTaxonomy(
        canonical_id="jvm_java",
        display_name="Java (JVM)",
        category="language",
        ecosystem="jvm",
        aliases=["java", "java 8", "java 11", "java 17", "java 21", "openjdk", "core java", "java se", "java ee", "jakarta ee"]
    ),
    TechTaxonomy(
        canonical_id="jvm_spring",
        display_name="Spring Boot / Spring Framework",
        category="framework",
        ecosystem="jvm",
        aliases=["spring", "spring boot", "springboot", "spring mvc", "spring cloud", "spring data", "spring security"]
    ),
    TechTaxonomy(
        canonical_id="jvm_hibernate",
        display_name="Hibernate / JPA",
        category="framework",
        ecosystem="jvm",
        aliases=["hibernate", "jpa"]
    ),
    TechTaxonomy(
        canonical_id="jvm_quarkus",
        display_name="Quarkus",
        category="framework",
        ecosystem="jvm",
        aliases=["quarkus"]
    ),
    TechTaxonomy(
        canonical_id="jvm_kotlin",
        display_name="Kotlin",
        category="language",
        ecosystem="jvm",
        aliases=["kotlin"]
    ),

    # JavaScript / TypeScript Ecosystem
    TechTaxonomy(
        canonical_id="js_javascript",
        display_name="JavaScript (ECMAScript)",
        category="language",
        ecosystem="js_ts",
        aliases=["javascript", "js", "vanilla js", "ecmascript", "es6", "es2020"]
    ),
    TechTaxonomy(
        canonical_id="js_typescript",
        display_name="TypeScript",
        category="language",
        ecosystem="js_ts",
        aliases=["typescript", "ts"]
    ),
    TechTaxonomy(
        canonical_id="js_nodejs",
        display_name="Node.js",
        category="framework",
        ecosystem="js_ts",
        aliases=["node.js", "nodejs", "node"]
    ),
    TechTaxonomy(
        canonical_id="js_react",
        display_name="React",
        category="framework",
        ecosystem="js_ts",
        aliases=["react", "react.js", "reactjs", "react native"]
    ),
    TechTaxonomy(
        canonical_id="js_vue",
        display_name="Vue.js",
        category="framework",
        ecosystem="js_ts",
        aliases=["vue", "vue.js", "vuejs", "vue3", "nuxt"]
    ),
    TechTaxonomy(
        canonical_id="js_angular",
        display_name="Angular",
        category="framework",
        ecosystem="js_ts",
        aliases=["angular", "angularjs", "angular 2+"]
    ),
    TechTaxonomy(
        canonical_id="js_nestjs",
        display_name="Nest.js",
        category="framework",
        ecosystem="js_ts",
        aliases=["nest.js", "nestjs", "nest"]
    ),
    TechTaxonomy(
        canonical_id="js_nextjs",
        display_name="Next.js",
        category="framework",
        ecosystem="js_ts",
        aliases=["next.js", "nextjs", "next"]
    ),
    TechTaxonomy(
        canonical_id="js_express",
        display_name="Express.js",
        category="framework",
        ecosystem="js_ts",
        aliases=["express", "express.js", "expressjs"]
    ),

    # PHP Ecosystem
    TechTaxonomy(
        canonical_id="php_language",
        display_name="PHP",
        category="language",
        ecosystem="php",
        aliases=["php", "php 7", "php 8", "php7", "php8"]
    ),
    TechTaxonomy(
        canonical_id="php_laravel",
        display_name="Laravel",
        category="framework",
        ecosystem="php",
        aliases=["laravel", "eloquent"]
    ),
    TechTaxonomy(
        canonical_id="php_symfony",
        display_name="Symfony",
        category="framework",
        ecosystem="php",
        aliases=["symfony"]
    ),
    TechTaxonomy(
        canonical_id="php_twig",
        display_name="Twig",
        category="framework",
        ecosystem="php",
        aliases=["twig"]
    ),

    # Python Ecosystem
    TechTaxonomy(
        canonical_id="py_python",
        display_name="Python",
        category="language",
        ecosystem="python",
        aliases=["python", "python 3", "python3", "py"]
    ),
    TechTaxonomy(
        canonical_id="py_django",
        display_name="Django",
        category="framework",
        ecosystem="python",
        aliases=["django", "django rest framework", "drf"]
    ),
    TechTaxonomy(
        canonical_id="py_fastapi",
        display_name="FastAPI",
        category="framework",
        ecosystem="python",
        aliases=["fastapi"]
    ),
    TechTaxonomy(
        canonical_id="py_flask",
        display_name="Flask",
        category="framework",
        ecosystem="python",
        aliases=["flask"]
    ),
    TechTaxonomy(
        canonical_id="py_datascience",
        display_name="Python Data Science / ML",
        category="framework",
        ecosystem="python",
        aliases=["pandas", "numpy", "scikit-learn", "sklearn", "pytorch", "tensorflow", "keras"]
    ),

    # .NET / C# Ecosystem
    TechTaxonomy(
        canonical_id="dotnet_csharp",
        display_name="C# (.NET)",
        category="language",
        ecosystem="dotnet",
        aliases=["c#", "csharp", ".net", "dotnet", ".net core", "asp.net", "asp.net core", "entity framework", "ef core", "linq"]
    ),

    # Golang Ecosystem
    TechTaxonomy(
        canonical_id="go_golang",
        display_name="Golang",
        category="language",
        ecosystem="golang",
        aliases=["golang", "go"]
    ),

    # Rust Ecosystem
    TechTaxonomy(
        canonical_id="rust_language",
        display_name="Rust",
        category="language",
        ecosystem="rust",
        aliases=["rust", "tokio", "actix"]
    ),

    # Ruby Ecosystem
    TechTaxonomy(
        canonical_id="ruby_language",
        display_name="Ruby on Rails",
        category="language",
        ecosystem="ruby",
        aliases=["ruby", "rails", "ruby on rails"]
    ),

    # C / C++
    TechTaxonomy(
        canonical_id="cpp_language",
        display_name="C++",
        category="language",
        ecosystem="cpp",
        aliases=["c++", "cpp"]
    ),

    # Databases
    TechTaxonomy(
        canonical_id="db_sql",
        display_name="SQL",
        category="database",
        ecosystem="data",
        aliases=["sql", "relational database"]
    ),
    TechTaxonomy(
        canonical_id="db_postgres",
        display_name="PostgreSQL",
        category="database",
        ecosystem="data",
        aliases=["postgresql", "postgres", "psql"]
    ),
    TechTaxonomy(
        canonical_id="db_mysql",
        display_name="MySQL",
        category="database",
        ecosystem="data",
        aliases=["mysql", "mariadb"]
    ),
    TechTaxonomy(
        canonical_id="db_sqlserver",
        display_name="SQL Server",
        category="database",
        ecosystem="data",
        aliases=["sql server", "mssql", "tsql"]
    ),
    TechTaxonomy(
        canonical_id="db_oracle",
        display_name="Oracle DB",
        category="database",
        ecosystem="data",
        aliases=["oracle", "pl/sql", "plsql", "oracle database"]
    ),
    TechTaxonomy(
        canonical_id="db_redis",
        display_name="Redis",
        category="database",
        ecosystem="data",
        aliases=["redis"]
    ),
    TechTaxonomy(
        canonical_id="db_mongodb",
        display_name="MongoDB",
        category="database",
        ecosystem="data",
        aliases=["mongodb", "mongo"]
    ),
    TechTaxonomy(
        canonical_id="db_supabase",
        display_name="Supabase",
        category="database",
        ecosystem="data",
        aliases=["supabase"]
    ),

    # Cloud & DevOps
    TechTaxonomy(
        canonical_id="ops_docker",
        display_name="Docker / Containers",
        category="devops",
        ecosystem="ops",
        aliases=["docker", "containers", "containerization"]
    ),
    TechTaxonomy(
        canonical_id="ops_kubernetes",
        display_name="Kubernetes",
        category="devops",
        ecosystem="ops",
        aliases=["kubernetes", "k8s"]
    ),
    TechTaxonomy(
        canonical_id="ops_aws",
        display_name="AWS Cloud",
        category="cloud",
        ecosystem="ops",
        aliases=["aws", "amazon web services", "s3", "ec2", "lambda"]
    ),
    TechTaxonomy(
        canonical_id="ops_azure",
        display_name="Azure Cloud",
        category="cloud",
        ecosystem="ops",
        aliases=["azure", "microsoft azure"]
    ),
    TechTaxonomy(
        canonical_id="ops_gcp",
        display_name="Google Cloud (GCP)",
        category="cloud",
        ecosystem="ops",
        aliases=["gcp", "google cloud", "google cloud platform"]
    ),
    TechTaxonomy(
        canonical_id="ops_git",
        display_name="Git / Version Control",
        category="devops",
        ecosystem="ops",
        aliases=["git", "github", "gitlab", "bitbucket"]
    )
]


class NormalizedSemanticProfile(BaseModel):
    raw_text: str
    canonical_entities: List[str] = Field(default_factory=list) # e.g. ["jvm_java", "jvm_spring"]
    display_entities: List[str] = Field(default_factory=list) # e.g. ["Java (JVM)", "Spring Boot"]
    primary_languages: List[str] = Field(default_factory=list) # e.g. ["Java (JVM)"]
    ecosystems_present: Set[str] = Field(default_factory=set) # e.g. {"jvm"}
    normalized_token_stream: str = "" # Normalized text string for vectorization


class SemanticTaxonomyNormalizer:
    """
    Deterministic semantic normalizer and classifier for tech stacks,
    disambiguating conflicting terms (e.g. Java vs JavaScript, C# vs C)
    before vectorization and ATS matching.
    """

    def __init__(self):
        self._taxonomy = TECH_TAXONOMY_REGISTRY

    def normalize_text_entities(self, text: str) -> NormalizedSemanticProfile:
        """Extracts and canonicalizes technologies with strict disambiguation."""
        if not text:
            return NormalizedSemanticProfile(raw_text="")

        t_clean = f" {text.lower()} "
        # Replace punctuation that could merge words, preserving dots in node.js, c#, .net
        t_clean = re.sub(r'[/,;:()|\\]', ' ', t_clean)

        matched_entities: Dict[str, TechTaxonomy] = {}

        # 1. First pass: Handle explicit disambiguation words (e.g., JavaScript vs Java)
        has_javascript = bool(re.search(r'\b(javascript|js|vanilla\s*js|ecmascript|typescript|ts|node\.?js|react|vue|angular|nestjs|nextjs)\b', t_clean))
        
        # Check Java (JVM) strictly ensuring it is NOT part of 'javascript'
        # Match 'java' only if NOT followed by 'script' or 'doc' or preceded by 'type'
        has_java = bool(re.search(r'\bjava\s*(8|11|17|21|se|ee)?\b(?!\s*script|\s*doc)', t_clean))

        for item in self._taxonomy:
            if item.canonical_id == "jvm_java":
                if has_java:
                    matched_entities[item.canonical_id] = item
                continue

            if item.canonical_id == "js_javascript":
                if has_javascript or any(re.search(r'\b' + re.escape(al) + r'\b', t_clean) for al in item.aliases):
                    matched_entities[item.canonical_id] = item
                continue

            # Standard alias match
            for alias in item.aliases:
                pattern = r'\b' + re.escape(alias) + r'\b'
                if re.search(pattern, t_clean):
                    matched_entities[item.canonical_id] = item
                    break

        canonical_ids = list(matched_entities.keys())
        display_names = [item.display_name for item in matched_entities.values()]
        primary_langs = [item.display_name for item in matched_entities.values() if item.category == "language"]
        ecosystems = set(item.ecosystem for item in matched_entities.values())

        # Construct normalized semantic token stream with prefixed tags for vectorization
        tokens = [f"__entity_{cid}__" for cid in canonical_ids]
        tokens += [f"__eco_{eco}__" for eco in ecosystems]
        # Include original text words
        words = re.findall(r'\b[a-zA-Z0-9_\+\#-]{2,}\b', text.lower())
        tokens += words
        normalized_stream = " ".join(tokens)

        return NormalizedSemanticProfile(
            raw_text=text,
            canonical_entities=canonical_ids,
            display_entities=display_names,
            primary_languages=primary_langs,
            ecosystems_present=ecosystems,
            normalized_token_stream=normalized_stream
        )

    def audit_stack_compatibility(
        self,
        candidate_profile: NormalizedSemanticProfile,
        job_profile: NormalizedSemanticProfile
    ) -> Tuple[bool, List[str], List[str]]:
        """
        Audits hard technical stack compatibility.
        Returns: (is_compatible: bool, matched_canonical: List[str], missing_mandatory_languages: List[str])
        """
        matched = []
        missing_mandatory_languages = []

        # Find overlapping canonical entities
        cand_entities_set = set(candidate_profile.canonical_entities)
        for entity_id in job_profile.canonical_entities:
            if entity_id in cand_entities_set:
                matched.append(entity_id)

        # Check mandatory language ecosystems
        # If job mandates a primary language ecosystem (JVM, .NET, Python, Golang, Rust, Ruby, PHP)
        PRIMARY_LANGUAGE_ECOSYSTEMS = {"jvm", "dotnet", "python", "golang", "rust", "ruby", "php"}
        job_lang_ecosystems = job_profile.ecosystems_present.intersection(PRIMARY_LANGUAGE_ECOSYSTEMS)

        for eco in job_lang_ecosystems:
            # Check if job specifically requires languages or core backend frameworks from this ecosystem
            job_eco_entities = [item for item in self._taxonomy if item.canonical_id in job_profile.canonical_entities and item.ecosystem == eco]
            cand_eco_entities = [item for item in self._taxonomy if item.canonical_id in candidate_profile.canonical_entities and item.ecosystem == eco]

            if job_eco_entities and not cand_eco_entities:
                missing_labels = [item.display_name for item in job_eco_entities if item.category == "language" or item.canonical_id in ("jvm_spring", "dotnet_csharp", "php_laravel")]
                if not missing_labels:
                    missing_labels = [item.display_name for item in job_eco_entities]
                missing_mandatory_languages.append(" / ".join(missing_labels))

        is_compatible = len(missing_mandatory_languages) == 0
        return is_compatible, matched, missing_mandatory_languages


semantic_normalizer = SemanticTaxonomyNormalizer()
