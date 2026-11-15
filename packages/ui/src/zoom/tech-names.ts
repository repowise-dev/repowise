/**
 * Display names for languages, frameworks and external packages. Pure.
 *
 * The backend sends technology names title-cased from the raw package
 * ("Fastapi", "Next", "Sqlalchemy") and a file's language lowercase
 * ("python"). Every surface that shows one (card chips, the card footer, the
 * map key, the detail sheet) goes through `techName`, so a card and the sheet
 * beside it never spell one technology two ways.
 */

/** Lowercase raw name -> proper name, for names a plain capital gets wrong. */
const DISPLAY: Record<string, string> = {
  python: "Python",
  py: "Python",
  typescript: "TypeScript",
  ts: "TypeScript",
  tsx: "TypeScript",
  javascript: "JavaScript",
  js: "JavaScript",
  jsx: "JavaScript",
  csharp: "C#",
  "c#": "C#",
  cpp: "C++",
  "c++": "C++",
  objc: "Objective-C",
  php: "PHP",
  sql: "SQL",
  html: "HTML",
  css: "CSS",
  scss: "SCSS",
  json: "JSON",
  yaml: "YAML",
  toml: "TOML",
  xml: "XML",
  graphql: "GraphQL",
  cobol: "COBOL",
  next: "Next.js",
  nextjs: "Next.js",
  "next.js": "Next.js",
  nuxt: "Nuxt",
  nestjs: "NestJS",
  vue: "Vue",
  fastapi: "FastAPI",
  sqlalchemy: "SQLAlchemy",
  pydantic: "Pydantic",
  numpy: "NumPy",
  pytorch: "PyTorch",
  torch: "PyTorch",
  tensorflow: "TensorFlow",
  httpx: "HTTPX",
  aiohttp: "aiohttp",
  grpc: "gRPC",
  grpcio: "gRPC",
  redis: "Redis",
  postgres: "PostgreSQL",
  postgresql: "PostgreSQL",
  psycopg2: "psycopg2",
  mysql: "MySQL",
  mongodb: "MongoDB",
  pymongo: "PyMongo",
  sqlite: "SQLite",
  sqlite3: "SQLite",
  boto3: "AWS SDK",
  "aws-sdk": "AWS SDK",
  graphene: "Graphene",
  jquery: "jQuery",
  nodejs: "Node.js",
  node: "Node.js",
};

const seen = new Map<string, string>();

/**
 * The display name of a technology, language or package. A known name gets
 * its proper spelling; anything else keeps its own casing with a leading
 * capital, so a name the backend already spells right is left alone. Memoised
 * (the vocabulary is a few dozen names per map), so a frame builds no string.
 */
export function techName(raw: string): string {
  const hit = seen.get(raw);
  if (hit !== undefined) return hit;
  const trimmed = raw.trim();
  const out = DISPLAY[trimmed.toLowerCase()] ?? trimmed.charAt(0).toUpperCase() + trimmed.slice(1);
  seen.set(raw, out);
  return out;
}
