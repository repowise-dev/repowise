import { JSXElement } from 'typescript';

// ... existing code ...

function analyzeDeadCode(node: JSXElement, context: AnalysisContext): void {
  // ... existing code ...

  if (node.openingElement.attributes.some(attr => attr.name.text === 'if')) {
    // Skip analysis for components rendered behind a prop guard
    return;
  }

  // ... rest of the function ...
}