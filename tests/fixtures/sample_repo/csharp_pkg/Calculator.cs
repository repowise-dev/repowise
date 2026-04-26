using System;
using Sample.Models;

namespace Sample
{
    /// <summary>Calculator for basic arithmetic.</summary>
    public class Calculator
    {
        public double Add(double x, double y)
        {
            return x + y;
        }

        public double Subtract(double x, double y)
        {
            return x - y;
        }

        private void Helper() { }
    }
}
