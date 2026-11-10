# Dashboard design system

The dashboard is a research workspace for synthetic T+1 settlement and FX funding experiments. Its visual hierarchy follows the work: create data, train, evaluate, compare, inspect evidence. Blue identifies actions and selections; status colors always accompany words. Technical identifiers and extended methodology stay in details rather than competing with the next action.

## Structure and component audit

- `src/main.tsx`: application shell, navigation, Trades, Funding, Scenarios and Model health.
- `src/Experiments.tsx`: guided preparation, durable jobs, comparison and exports.
- `src/Evaluation.tsx`: date/time controls and trade schedule; calendar popover uses Radix.
- `src/components.tsx`: shared status, statistics, theme control and navigation icons.
- `src/components/ui/`: existing shadcn primitives, including the application-specific Radix Select. Keep keyboard navigation, accessible names, portal collision handling and controlled state.
- `src/globals.css`: Tailwind imports, global semantic colors, typography and base elements.
- `tailwind.config.js`: shared font utility configuration, explicitly loaded with Tailwind 4's `@config`. Semantic utility colors are mapped by `@theme inline` in globals.css.
- `src/style.css`: dashboard layout, component placement, responsive and container rules. Imports globals.css once.
- `components.json`: points shadcn generation at this configuration and global stylesheet.

All 15 UI component modules are referenced by the application or another primitive. Buttons, inputs, labels and selects belong in workflow controls; alerts display errors; cards summarize metrics; tables and charts display evidence; badges label states; breadcrumbs, separators and sidebar provide navigation. Sheet and Tooltip support mobile/collapsed sidebar navigation. Skeleton is retained as part of the sidebar's composable loading API. Not every exported shadcn variant is currently rendered; retain those reusable primitive APIs. Removed the unreferenced custom `RiskMeter` and its CSS, which represented the retired rule-score display. No component library replacement or new dependency is needed.

## Six core colors

| Token | Hex | Purpose |
| --- | --- | --- |
| Ink | `#090B0F` | Dark canvas and light-theme text |
| White | `#FFFFFF` | Light surfaces and dark-theme text mixtures |
| Blue | `#1659DB` | Actions, selection and focus |
| Slate | `#566278` | Supporting text and neutral surface mixtures |
| Green | `#17654C` | Successful or verified states |
| Red | `#B52D46` | Errors and failures |

Theme-dependent surfaces, borders, foregrounds and status backgrounds derive from these through `color-mix`. Components consume semantic roles such as `--foreground`, never a fixed light/dark color. Destructive buttons have their own foreground role so text remains readable in both themes. Chart-only amber and purple extend the categorical data palette for five policies; they are not interface or status tokens. Legends retain foreground text and policy names.

## Typography

IBM Plex Sans is locally bundled for the entire interface; IBM Plex Mono is for code and raw records only. Financial columns use tabular figures and right alignment. Avoid uppercase labels, decorative monospace captions and text justified on both edges.

| Role | Size | Usage |
| --- | --- | --- |
| Caption | 12px | Badges, supporting metadata |
| Body | 14px | Controls, tables, labels, explanations |
| Lead | 16px | Subheadings |
| Section | 20px | Section headings |
| Metric | 24px | Compact key numbers |
| Title | 32px | Page heading; shrinks to 24px on mobile |

Body line height is 1.5, explanatory paragraphs 1.65. Keep explanation lines below 80 characters. Heading weight is 600; control text is 500. Tailwind's `text-xs`, `text-sm`, `text-base`, `text-xl`, `text-2xl`, and `text-3xl` share these values.

## Layout principles

Desktop has a persistent collapsible sidebar and a content area capped at 1600px. Page titles and controls are left aligned; page actions and export buttons sit at the right when space allows. Tables preserve columns and scroll within a keyboard-focusable region. Mobile navigation uses the existing shadcn Sheet; forms stack, toolbar actions wrap, and optional evidence stays collapsible. Content container queries also adapt when the desktop sidebar expands.

```text
Desktop                         Mobile
sidebar | title       actions   menu           appearance
        | workflow              title
        | controls              actions
        | results / evidence    stacked controls
                                scrollable results
```

Use 4px-based spacing, 44px primary controls and restrained 8–12px radii. Reserve shadows for floating menus and calendar popovers. Selection uses a blue-tinted surface and a thin outline, without a thick side accent. Focus remains visible, and reduced-motion preferences disable animation. Do not replace meaningful financial tables with disconnected mobile cards.

## Verification

Run `npm run build --prefix frontend` and `npm run test:e2e --prefix frontend` from the repository root. Browser coverage includes both themes, text contrast and font sizes, keyboard dropdowns, mobile navigation, narrow screens, expanded/collapsed sidebars, page placement, real fixture-backed training and result export. Provider responses are frozen for tests; user artifacts are untouched.

## Research workspace layout

`WorkflowSection.tsx` places a stage heading and short explanation beside its working controls on wide desktop views. Below 950px of available content width, context stacks above controls. Four persistent step links navigate within Experiments; their current-location state follows visible sections and does not claim job completion. Prediction targets, all resolved assumptions, and detailed evidence remain expandable.

Below 768px, five persistent bottom navigation actions provide direct access to Experiments, Trades, Funding, Scenarios and Models. Safe-area padding protects controls on phones; content includes bottom clearance. The sidebar drawer remains available. Navigation has explicit accessible names/current-page state, and the workspace provides a skip link. Selection and focus transitions are brief; reduced-motion preferences disable them.

The application uses functional data surfaces rather than decorative imagery: editable settings, tabular outcomes and model evidence form the visual hierarchy. Avoid adding marketing heroes or imagery that competes with financial results.
