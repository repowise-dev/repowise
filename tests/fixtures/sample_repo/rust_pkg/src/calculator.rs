//! Stateful calculator with history tracking.

use crate::models::{CalculationRecord, Operation};

/// Error returned when division by zero is attempted.
#[derive(Debug, thiserror::Error)]
pub enum CalculatorError {
    #[error("division by zero")]
    DivisionByZero,
}

/// A stateful calculator that records all performed operations.
pub struct Calculator {
    history: Vec<CalculationRecord>,
}

impl Calculator {
    /// Creates a new, empty `Calculator`.
    pub fn new() -> Self {
        Self { history: Vec::new() }
    }

    /// Adds `x` and `y`, records the result, and returns it.
    pub fn add(&mut self, x: f64, y: f64) -> f64 {
        let result = x + y;
        self.record(Operation::Add, x, y, result);
        result
    }

    /// Subtracts `y` from `x`, records the result, and returns it.
    pub fn subtract(&mut self, x: f64, y: f64) -> f64 {
        let result = x - y;
        self.record(Operation::Subtract, x, y, result);
        result
    }

    /// Multiplies `x` by `y`, records the result, and returns it.
    pub fn multiply(&mut self, x: f64, y: f64) -> f64 {
        let result = x * y;
        self.record(Operation::Multiply, x, y, result);
        result
    }

    /// Divides `x` by `y`. Returns [`CalculatorError::DivisionByZero`] if `y == 0`.
    pub fn divide(&mut self, x: f64, y: f64) -> Result<f64, CalculatorError> {
        if y == 0.0 {
            return Err(CalculatorError::DivisionByZero);
        }
        let result = x / y;
        self.record(Operation::Divide, x, y, result);
        Ok(result)
    }

    /// Returns a slice of all recorded calculations.
    pub fn history(&self) -> &[CalculationRecord] {
        &self.history
    }

    /// Clears the history.
    pub fn clear_history(&mut self) {
        self.history.clear();
    }

    fn record(&mut self, op: Operation, x: f64, y: f64, result: f64) {
        self.history.push(CalculationRecord::new(op, x, y, result));
    }
}

impl Default for Calculator {
    fn default() -> Self {
        Self::new()
    }
}
