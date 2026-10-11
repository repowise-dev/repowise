function deeplyNested(x) {
  if (x > 0) {
    for (let i = 0; i < x; i++) {
      if (i % 2 === 0) {
        while (i > 0) {
          try {
            if (i === 5) {
              return i;
            }
          } catch (e) {}
          i--;
        }
      }
    }
  }
  return 0;
}

function shallow(x) {
  return x + 1;
}
