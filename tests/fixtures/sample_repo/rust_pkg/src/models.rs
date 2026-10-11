//! Domain models for the sample calculator.

use std::fmt;

/// Supported arithmetic operations.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Operation {
    Add,
    Subtract,
    Multiply,
    Divide,
}

impl fmt::Display for Operation {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::Add => write!(f, "add"),
            Self::Subtract => write!(f, "subtract"),
            Self::Multiply => write!(f, "multiply"),
            Self::Divide => write!(f, "divide"),
        }
    }
}

/// A single recorded arithmetic calculation.
#[derive(Debug, Clone)]
pub struct CalculationRecord {
    pub operation: Operation,
    pub x: f64,
    pub y: f64,
    pub result: f64,
}

impl CalculationRecord {
    /// Create a new record.
    pub fn new(operation: Operation, x: f64, y: f64, result: f64) -> Self {
        Self { operation, x, y, result }
    }

    /// Returns a human-readable summary of this record.
    pub fn summary(&self) -> String {
        format!("{:.2} {} {:.2} = {:.2}", self.x, self.operation, self.y, self.result)
    }
}
