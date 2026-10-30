type Props = {quote: string; locator: string; location?: string};

export function SourceEvidence({quote, locator, location = ''}: Props) {
    const imported = location.startsWith('snapshot:');
    const label = imported ? 'Imported reference' : location || 'Source';
    const structured = ['{', '['].some(prefix => quote.trimStart().startsWith(prefix));
    const evidence = <blockquote><mark>{quote}</mark><footer>{locator} · {location}</footer></blockquote>;
    if (!structured && quote.length <= 360) return evidence;
    return <details className="source-evidence">
        <summary>View source evidence · {label}</summary>
        {evidence}
    </details>;
}
