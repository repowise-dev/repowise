//! Sample Rust calculator library.
//!
//! Provides a [`Calculator`] struct and associated arithmetic operations,
//! plus the [`Operation`] enum and [`CalculationRecord`] for history tracking.

pub mod calculator;
pub mod models;

pub use calculator::Calculator;
pub use models::{CalculationRecord, Operation};
