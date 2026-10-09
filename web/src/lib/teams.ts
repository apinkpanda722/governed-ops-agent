export const TEAMS: Record<string, string> = {
    "platform": "플랫폼개발팀",
    "payments": "결제서비스팀",
    "data-ai": "데이터AI팀",
    "sre": "SRE인프라팀",
    "security": "정보보안팀",
    "hr": "인사팀",
    "finance": "재무팀",
    "legal": "법무컴플라이언스팀",
};
export const TEAM_ORDER = ["platform", "payments", "data-ai", "sre", "security", "hr", "finance", "legal"] as const;
export function parseUserLabel(label: string): {team: string; grade: number} | null {
    const match = /^demo-(.+)-g(\d+)$/.exec(label);
    return match ? {team: match[1], grade: Number(match[2])} : null;
}
export function userDisplay(label: string): string {
    const parsed = parseUserLabel(label);
    return parsed && Object.hasOwn(TEAMS, parsed.team) ? `${TEAMS[parsed.team]} · ${parsed.grade}급` : label;
}
