import type {ReactNode} from 'react';

/** One task per section, with context beside the working surface on desktop. */
export function WorkflowSection({id, step, title, description, children}: {
  id: string; step: string; title: string; description: string; children: ReactNode;
}) {
  return <section className="workflow-section" id={id} aria-labelledby={`${id}-title`}>
    <div className="workflow-section-heading">
      <span className="workflow-section-step">Step {step}</span>
      <h2 id={`${id}-title`}>{title}</h2>
      <p>{description}</p>
    </div>
    <div className="workflow-section-body">{children}</div>
  </section>;
}
