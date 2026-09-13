type Props = { title?: string; children?: unknown };

export function Component(props: Props): JSX.Element {
    const title = props.title ?? "untitled";
    const header = <h1 data-kind="title">{title}</h1>;
    const body = <section>{props.children}</section>;
    return <main>{header}{body}</main>;
}
