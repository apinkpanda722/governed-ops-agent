import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
const safeUrl = (url: string) => /^https?:\/\//i.test(url) ? url : "";
export function Markdown({children}: {children: string}) {
    return <div className="min-w-0 space-y-3 break-words"><ReactMarkdown skipHtml remarkPlugins={[remarkGfm]} disallowedElements={["img","script","style","iframe","object","embed","form","input"]} urlTransform={safeUrl} components={{
        a: ({href,children}) => href && safeUrl(href) ? <a href={href} target="_blank" rel="noopener noreferrer nofollow" className="text-primary underline">{children}</a> : <>{children}</>,
        h1: ({children}) => <h1 className="mt-5 text-2xl font-semibold">{children}</h1>,
        h2: ({children}) => <h2 className="mt-4 text-xl font-semibold">{children}</h2>,
        h3: ({children}) => <h3 className="mt-3 text-lg font-semibold">{children}</h3>,
        h4: ({children}) => <h4 className="font-semibold">{children}</h4>,
        h5: ({children}) => <h5 className="font-semibold">{children}</h5>,
        h6: ({children}) => <h6 className="font-semibold">{children}</h6>,
        p: ({children}) => <p className="leading-7">{children}</p>,
        ul: ({children}) => <ul className="list-disc space-y-1 pl-6">{children}</ul>,
        ol: ({children}) => <ol className="list-decimal space-y-1 pl-6">{children}</ol>,
        li: ({children}) => <li className="leading-7">{children}</li>,
        blockquote: ({children}) => <blockquote className="border-l-2 pl-4 text-muted-foreground">{children}</blockquote>,
        hr: () => <hr className="my-4 border-border"/>,
        code: ({children}) => <code className="rounded bg-muted px-1 py-0.5 font-mono text-sm">{children}</code>,
        pre: ({children}) => <pre className="min-w-0 overflow-x-auto rounded-lg bg-muted p-4">{children}</pre>,
        table: ({children}) => <div className="min-w-0 overflow-x-auto"><table className="w-full border-collapse text-sm">{children}</table></div>,
        thead: ({children}) => <thead className="bg-muted font-semibold">{children}</thead>,
        th: ({children}) => <th className="border px-3 py-2 text-left">{children}</th>,
        td: ({children}) => <td className="border px-3 py-2">{children}</td>,
    }}>{children}</ReactMarkdown></div>;
}
