import React, { useState } from 'react';
import { ChevronDownIcon, ChevronUpIcon } from './Icons';

/**
 * Generic collapsible list that shows an initial count and expands with 'Show more'.
 */
export function CollapsibleList({
  items = [],
  initialCount = 3,
  renderItem,
  listClassName = '',
  itemClassName = '',
  moreLabel = 'Show more',
  lessLabel = 'Show less',
}) {
  const [expanded, setExpanded] = useState(false);

  if (!items || items.length === 0) return null;

  const visibleItems = expanded ? items : items.slice(0, initialCount);
  const canToggle = items.length > initialCount;

  return (
    <div className="collapsible-list-wrap">
      <ul className={`collapsible-list ${listClassName}`}>
        {visibleItems.map((item, index) => (
          <li key={item.id || index} className={itemClassName}>
            {renderItem(item, index)}
          </li>
        ))}
      </ul>

      {canToggle && (
        <button
          type="button"
          onClick={() => setExpanded(!expanded)}
          className="btn-toggle-expand"
        >
          <span>{expanded ? lessLabel : `${moreLabel} (${items.length - initialCount} more)`}</span>
          {expanded ? <ChevronUpIcon size="sm" /> : <ChevronDownIcon size="sm" />}
        </button>
      )}
    </div>
  );
}

