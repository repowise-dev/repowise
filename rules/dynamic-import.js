const rule = {
  meta: {
    messages: {
      unused: 'Unused dynamic import() target'
    }
  },
  create(context) {
    return {
      'import()': {
        exit(node) {
          if (node.type === 'CallExpression' && node.arguments[0].type === 'Identifier') {
            context.report({
              node: node, 
              messageId: 'unused'
            });
          }
        }
      }
    };
  }
};