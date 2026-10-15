import { useId } from 'react';
import { Select as SelectPrimitive } from 'radix-ui';
import { Check, ChevronDown, ChevronUp } from 'lucide-react';

export type SelectOption = { value: string; label: string; disabled?: boolean };
const EMPTY_VALUE = '__empty_selection__';

export function Select({label, value, onValueChange, options, placeholder = 'Select an option', disabled = false}: {
  label: string;
  value: string;
  onValueChange: (value: string) => void;
  options: SelectOption[];
  placeholder?: string;
  disabled?: boolean;
}) {
  const id = useId();
  return <SelectPrimitive.Root value={value || EMPTY_VALUE} onValueChange={next => onValueChange(next === EMPTY_VALUE ? '' : next)} disabled={disabled}>
    <SelectPrimitive.Trigger id={id} aria-label={label} data-slot="select-trigger" className="select-trigger">
      <SelectPrimitive.Value placeholder={placeholder}/><SelectPrimitive.Icon asChild><ChevronDown size={16}/></SelectPrimitive.Icon>
    </SelectPrimitive.Trigger>
    <SelectPrimitive.Portal><SelectPrimitive.Content data-slot="select-content" className="select-content" position="popper" sideOffset={6} collisionPadding={12}>
      <SelectPrimitive.ScrollUpButton className="select-scroll"><ChevronUp size={16}/></SelectPrimitive.ScrollUpButton>
      <SelectPrimitive.Viewport className="select-viewport">{options.map(option => <SelectPrimitive.Item data-slot="select-item" className="select-item" key={option.value} value={option.value || EMPTY_VALUE} disabled={option.disabled}>
        <SelectPrimitive.ItemText>{option.label}</SelectPrimitive.ItemText><SelectPrimitive.ItemIndicator className="select-check"><Check size={15}/></SelectPrimitive.ItemIndicator>
      </SelectPrimitive.Item>)}</SelectPrimitive.Viewport>
      <SelectPrimitive.ScrollDownButton className="select-scroll"><ChevronDown size={16}/></SelectPrimitive.ScrollDownButton>
    </SelectPrimitive.Content></SelectPrimitive.Portal>
  </SelectPrimitive.Root>;
}
